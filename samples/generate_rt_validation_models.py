from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import cadquery as cq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.types import EmitterAimSpec, EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec

DEFAULT_OUTPUT = ROOT / "outputs" / "RT_validation_kit_2026-09-20"


def box(minimum, maximum):
    dimensions = tuple(upper - lower for lower, upper in zip(minimum, maximum))
    center = tuple((upper + lower) / 2 for lower, upper in zip(minimum, maximum))
    return cq.Workplane("XY").box(*dimensions).translate(center)


def mirror(center, rotation_deg):
    return box((-4, -4, 0), (4, 4, 0.5)).rotate((0, 0, 0), (0, 1, 0), rotation_deg).translate(center)


def source(center, *, alpha=0, beta=0, upper=0, lower=0, size=0.01, axes=None):
    horizontal, vertical = axes or ((1, 0, 0), (0, 1, 0))
    return EmitterSpec(
        emitter_id="source", emitter_type="datum_plane", center=center,
        u_axis=horizontal, v_axis=vertical, width_mm=size, height_mm=size,
        power_mode="total", power_lumen=1, ray_count=8192, seed=20260920,
        aim=EmitterAimSpec(enabled=True, mode="sphere", distribution="uniform_solid_angle",
                           sphere_upper_deg=upper, sphere_lower_deg=lower,
                           sphere_alpha_deg=alpha, sphere_beta_deg=beta),
    ).to_dict()


def detector(center, normal, *, width=2, height=2, resolution=(16, 16)):
    horizontal, vertical = (
        ((0, 0, 1), (0, 1, 0)) if normal == (-1, 0, 0)
        else ((1, 0, 0), (0, -1, 0))
    )
    return ReceiverSpec(
        receiver_id="observer", center=center, normal=normal,
        u_axis=horizontal, v_axis=vertical,
        width_mm=width, height_mm=height, resolution=resolution,
        acceptance_angle_deg=90,
    ).to_dict()


def profile(name, reflectance, model="specular", sigma=12, specular_ratio=1):
    return OpticalProfile(
        name, reflectance, scatter_model=model, gaussian_sigma_deg=sigma,
        specular_ratio=specular_ratio, diffuse_ratio=1 - specular_ratio,
    ).to_dict()


def definitions():
    models = {}
    cases = []

    def add_case(identifier, model_id, emitter, receiver, profiles, assignments, depth, expected=None):
        cases.append({
            "id": identifier, "model": model_id + ".stp", "emitters": [emitter],
            "receivers": [receiver], "optical_profiles": profiles,
            "component_profiles": assignments,
            "config": RayTraceConfig(
                max_depth=depth, ray_count=8192, seed=20260920,
                epsilon_mm=1e-6, min_energy=1e-18, angle_dependent_reflectance=False,
                intersection_backend="bvh", store_ray_paths=True, max_stored_paths=12,
                primary_sampling_strategy="source", bounce_sampling_strategy="source",
            ).to_dict(),
            "expected": expected,
        })

    models["01_single_mirror"] = [("Mirror_A", mirror((0, 0, 10), -45))]
    add_case("01_single_mirror", "01_single_mirror", source((0, 0, 0)),
             detector((10, 0, 10), (-1, 0, 0)), [profile("rho_080", 0.8)],
             {"Mirror_A": "rho_080"}, 1, {"kind": "exact", "flux_lumen": 0.8, "reflections": 1})
    models["02_two_mirrors"] = [
        ("Mirror_A", mirror((0, 0, 10), -45)),
        ("Mirror_B", mirror((10, 0, 10), 135)),
    ]
    add_case("02_two_mirrors", "02_two_mirrors", source((0, 0, 0)),
             detector((10, 0, 20), (0, 0, -1)),
             [profile("rho_080", 0.8), profile("rho_050", 0.5)],
             {"Mirror_A": "rho_080", "Mirror_B": "rho_050"}, 2,
             {"kind": "exact", "flux_lumen": 0.4, "reflections": 2})
    inverse_root_two = 1 / math.sqrt(2)
    for number, reflections in enumerate((10, 100, 1000), start=3):
        identifier = f"{number:02d}_corridor_{reflections:04d}"
        length = 2 * reflections
        models[identifier] = [
            ("Wall_negative_Y", box((-1, -2, -2), (length + 1, -1, 2))),
            ("Wall_positive_Y", box((-1, 1, -2), (length + 1, 2, 2))),
        ]
        add_case(identifier, identifier,
                 source((0, 0, 0), alpha=-45, beta=90,
                        axes=((0, 0, 1), (inverse_root_two, -inverse_root_two, 0))),
                 detector((length, 0, 0), (-1, 0, 0), width=0.5, height=0.5),
                 [profile("rho_0999", 0.999)],
                 {"Wall_negative_Y": "rho_0999", "Wall_positive_Y": "rho_0999"},
                 reflections,
                 {"kind": "exact", "flux_lumen": 0.999**reflections, "reflections": reflections})

    models["06_scatter_plate"] = [("Scatter_plate", box((-10, -10, -1), (10, 10, 0)))]
    for model in ("lambertian", "gaussian", "mixed"):
        add_case("06_scatter_" + model, "06_scatter_plate",
                 source((0, 0, 5), beta=180),
                 detector((0, 0, 10), (0, 0, -1), width=10, height=10, resolution=(32, 32)),
                 [profile("scatter", 0.8, model=model, sigma=12, specular_ratio=0.4)],
                 {"Scatter_plate": "scatter"}, 1,
                 {"kind": "lambertian_quadrature", "reflectance": 0.8, "distance_mm": 10}
                 if model == "lambertian" else None)

    for number, gap in enumerate((0.5, 1.0, 2.0), start=7):
        identifier = f"{number:02d}_cavity_gap_{gap:.1f}".replace(".", "p")
        models[identifier] = [
            ("Back", box((-1, -10, -5), (0, 10, 5))),
            ("Side_negative_Y", box((0, -11, -5), (40, -10, 5))),
            ("Side_positive_Y", box((0, 10, -5), (40, 11, 5))),
            ("Bottom", box((0, -10, -6), (40, 10, -5))),
            ("Top", box((0, -10, 5), (40, 10, 6))),
            ("Exit_negative_Y", box((40, -10, -5), (41, -gap / 2, 5))),
            ("Exit_positive_Y", box((40, gap / 2, -5), (41, 10, 5))),
            ("Baffle_A", box((14, -10, -5), (15, 2, 5))),
            ("Baffle_B", box((25, -2, -5), (26, 10, 5))),
            ("Round_post", cq.Workplane("XY").circle(1.5).extrude(10).translate((20, 0, -5))),
        ]
        optical_profiles = [profile("wall", 0.95, "lambertian", specular_ratio=0),
                            profile("baffle", 0.8, "mixed", specular_ratio=0.4),
                            profile("post", 0.9, "gaussian", sigma=12)]
        assignments = {name: "baffle" if name.startswith("Baffle") else
                       "post" if name == "Round_post" else "wall" for name, _ in models[identifier]}
        add_case(identifier, identifier,
                 source((5, 0, 0), beta=90, lower=90, size=1, axes=((0, 1, 0), (0, 0, 1))),
                 detector((42, 0, 0), (-1, 0, 0), width=12, height=22, resolution=(12, 22)),
                 optical_profiles, assignments, 1000)

    models["10_source_chamber"] = []
    chamber_receivers = []
    for axis, axis_name in enumerate(("X", "Y", "Z")):
        for sign in (-1, 1):
            name = ("negative_" if sign < 0 else "positive_") + axis_name
            minimum, maximum = [-20, -20, -20], [20, 20, 20]
            minimum[axis], maximum[axis] = (-21, -20) if sign < 0 else (20, 21)
            models["10_source_chamber"].append((name, box(minimum, maximum)))
            center, normal = [0, 0, 0], [0, 0, 0]
            center[axis], normal[axis] = sign * 19, -sign
            horizontal = [0, 0, 0]
            horizontal[(axis + 1) % 3] = 1
            vertical = [0, 0, 0]
            vertical[(axis + 2) % 3] = -sign
            chamber_receivers.append(ReceiverSpec(
                receiver_id=name, center=center, normal=normal,
                u_axis=horizontal, v_axis=vertical,
                width_mm=38, height_mm=38, resolution=(32, 32),
            ).to_dict())
    source_variants = {}
    for distribution in ("lambertian", "isotropic", "gaussian"):
        emitter = source((0, 0, 0))
        emitter["aim"] = None
        emitter["direction_distribution"] = distribution
        if distribution == "gaussian":
            for sigma in (3, 12, 30):
                source_variants[f"gaussian_{sigma:02d}"] = {**emitter, "gaussian_sigma_deg": sigma}
        else:
            source_variants[distribution] = emitter
    for label, upper, lower, beta in (
        ("sphere_full", 0, 180, 0), ("sphere_hemisphere", 0, 90, 0),
        ("sphere_cone15", 0, 15, 0), ("sphere_annulus20_40", 20, 40, 0),
        ("sphere_collimated", 0, 0, 0), ("sphere_backward", 0, 15, 180),
    ):
        source_variants[label] = source((0, 0, 0), upper=upper, lower=lower, beta=beta)
    for shape in ("rectangle", "circle"):
        emitter = source((0, 0, 0))
        emitter["aim"] = EmitterAimSpec(
            enabled=True, mode="area", shape=shape, center=(0, 0, 19),
            width_mm=8, height_mm=4, radius_mm=4,
        ).__dict__
        source_variants["area_" + shape] = emitter
    emitter = source((0, 0, 0))
    emitter["aim"] = EmitterAimSpec(
        enabled=True, mode="area", center=(5, 0, 15),
        u_axis=(math.sqrt(3) / 2, 0, -0.5), v_axis=(0, 1, 0),
        width_mm=8, height_mm=4,
    ).__dict__
    source_variants["area_offset_tilt30"] = emitter
    for name, emitter in source_variants.items():
        add_case("10_source_" + name, "10_source_chamber", emitter, chamber_receivers[0],
                 [profile("black", 0)],
                 {part_name: "black" for part_name, _ in models["10_source_chamber"]}, 0,
                 {"kind": "source_capture", "flux_lumen": 1, "variant": name})
        cases[-1]["receivers"] = chamber_receivers
    return models, cases


def write_setup_guide(output, cases):
    text = [
        "# RT / LightTools 공통 검증 모델 — 설정표", "",
        "사내 TV 도면을 사용하지 않고 새로 만든 합성 검증 형상입니다. 단위는 모두 mm입니다.",
        "STEP에는 **실체 부품만** 있습니다. 광원·수광면·광학 물성은 STEP에 포함되지 않으므로 아래대로 따로 설정하세요.",
        "처음에는 01 → 02 → 03 순서로 비교하세요. 05는 의도적으로 2,000 mm 길이의 1,000회 반사 스트레스 모델입니다.", "",
        "## 공통 설정", "",
        "- 입사 총광속 **1 lm**, 편광·굴절·투과·회절 없음. SET luminance 입력 대신 Total flux를 사용합니다.",
        "- 1 lm은 계산 비교를 위한 정규화 입력이며 실제 TV의 밝기 조건을 뜻하지 않습니다.",
        "- 반사율은 각도·파장에 무관한 상수. 나머지 에너지는 손실이며 반사율 100%로 임의 변경하지 마세요.",
        "- 모든 부품의 모든 표면에 지정 물성을 적용합니다. 투명한 유전체로 지정하지 마세요.",
        "- 광원은 면 내 위치 균일. Aim Sphere 각도는 입체각 균일; 평행광은 Upper=Lower=0°입니다.",
        "- Alpha/Beta는 BitSam World 축 기준 Ry(Beta)Rx(Alpha)입니다. LT의 로컬 축 숫자를 그대로 복사하지 말고 아래 방향 벡터를 맞추세요.",
        "- 광원 좌표는 중심, u/v는 폭/높이 축입니다. 광원 위치 면의 기울기와 발광 방향은 별개입니다.",
        "- 수광면은 비반사 검출면, 수용 반각 90°. normal은 빛이 오는 쪽을 향합니다. 수광면 때문에 추가 반사가 생기지 않게 합니다.",
        "- 각 장면 첫 비교는 ROI 끄기 / 전체 CAD / 중요도 샘플링 끄기 / 자동 수렴 끄기입니다.",
        "- 챔버 내부 확인용 그림은 일부 벽을 생략했습니다. 실제 해석에서는 제공된 CAD 전체를 사용하고 Trace Off로 벽을 제거하지 마세요.",
        "- BitSam epsilon 0.000001 mm, 최소 Ray 광속 1e-18 lm. LT 종료 조건도 충분히 낮게 설정하고 실제 값을 기록하세요.",
        "- 아래 기본 Ray 수 8,192는 기능 검증용입니다. 산란/LT 통계 비교는 10만 → 100만 → 필요 시 1,000만과 독립 seed 5회 이상을 권장합니다.",
        "- 첫 비교 지표는 **Receiver 총 flux(lm)와 mean illuminance(lux)**입니다. nit_est를 LT 휘도와 직접 동일시하지 마세요.",
        "- Mean lux = 총 flux / Receiver 면적(m²). Peak는 동일 격자·좌표·필터·평활화 조건에서만 비교합니다.",
        "- Gaussian/Mixed는 제품별 분포 정의가 다를 수 있습니다. sigma=12°가 LT의 어떤 정의인지 확인 전에는 동일 물성으로 판정하지 않습니다.",
        "- 현재 BitSam Gaussian 광원은 θ=min(|Normal(0,σ)|,90°), φ=Uniform(0,360°)입니다. 단순히 단위 입체각당 exp(-θ²/2σ²)인 분포와 같지 않습니다.",
        "- models_manifest.json은 정확한 입력 계약이며 lt_results_template.csv에 LT 값을 기록합니다.", "",
    ]
    for case in cases:
        emitter, receiver = case["emitters"][0], case["receivers"][0]
        aim = emitter["aim"]
        alpha, beta = map(math.radians, ((aim or {}).get("sphere_alpha_deg", 0), (aim or {}).get("sphere_beta_deg", 0)))
        direction = [round(math.sin(beta) * math.cos(alpha), 9),
                     round(-math.sin(alpha), 9), round(math.cos(beta) * math.cos(alpha), 9)]
        text.extend([
            f"## {case['id']}", "", f"- STEP: `{case['model']}`",
            f"- 광원 중심: {emitter['center']}; 크기: {emitter['width_mm']} × {emitter['height_mm']}",
            f"- 광원 u / v: {emitter['u_axis']} / {emitter['v_axis']}",
            f"- 발광 설정: {('Aim ' + aim['mode']) if aim else emitter['direction_distribution']}",
            f"- 수광면 중심: {receiver['center']}; normal: {receiver['normal']}",
            f"- 수광면 크기: {receiver['width_mm']} × {receiver['height_mm']}; 격자: {receiver['resolution']}",
            f"- 수광면 u / v: {receiver['u_axis']} / {receiver['v_axis']}",
            f"- 최대 반사: {case['config']['max_depth']}; 총 Ray: {emitter['ray_count']}",
        ])
        if aim and aim["mode"] == "sphere":
            text.extend([f"- 발광 중심축: {direction}; 극각: {aim['sphere_upper_deg']}~{aim['sphere_lower_deg']}°; 방위각: 0~360°",
                         f"- BitSam Aim Sphere Alpha/Beta: {aim['sphere_alpha_deg']} / {aim['sphere_beta_deg']}°"])
        elif aim:
            text.append(f"- Target: {aim['shape']}, 중심 {aim['center']}, u {aim['u_axis']}, v {aim['v_axis']}, 폭/높이 {aim['width_mm']}/{aim['height_mm']}, 반경 {aim['radius_mm']}")
        elif emitter["direction_distribution"] == "gaussian":
            text.append(f"- Gaussian sigma: {emitter['gaussian_sigma_deg']}°; 중심축 +Z")
        if len(case["receivers"]) > 1:
            text.extend(["", "6개 수광면을 **동시에** 설치하세요. 중심 기준 ±19 mm, 각 크기 38×38, 32×32 격자입니다.",
                         "| Receiver | 중심 | normal | u | v |", "|---|---|---|---|---|"])
            for receiver_item in case["receivers"]:
                text.append(f"| {receiver_item['receiver_id']} | {receiver_item['center']} | {receiver_item['normal']} | {receiver_item['u_axis']} | {receiver_item['v_axis']} |")
            text.extend(["", "아래는 CAD 벽 물성입니다. 수광면은 검출 후 종료되며 CAD 벽보다 1 mm 안쪽에 있습니다."])
        text.extend(["", "| 부품 | 물성 | 반사율 | 산란 모델 | Gaussian σ | 경면/확산 비율 |",
                     "|---|---|---:|---|---:|---|"])
        profiles = {item["profile_id"]: item for item in case["optical_profiles"]}
        for name, profile_id in case["component_profiles"].items():
            item = profiles[profile_id]
            text.append(f"| {name} | {profile_id} | {item['reflectance']} | {item['scatter_model']} | {item['gaussian_sigma_deg']}° | {item['specular_ratio']} / {item['diffuse_ratio']} |")
        expected = case["expected"]
        if expected and expected["kind"] == "exact":
            text.extend(["", f"이론: {expected['reflections']}회 반사 후 **{expected['flux_lumen']:.12g} lm**. 상한을 {expected['reflections'] - 1}회로 낮추면 수광 0이어야 합니다."])
        elif expected and expected["kind"] == "lambertian_quadrature":
            text.extend(["", "Lambertian 기준값은 유한 발광 면적과 수광면 면적을 독립 수치 적분해 계산합니다. 무작위 샘플 결과에는 통계 오차가 있습니다."])
        elif expected:
            text.extend(["", "6면 수광 flux 합계는 1 lm. 전방/후방 분배와 heatmap 형상도 함께 비교합니다. 총량만 맞는다고 각도 분포까지 검증된 것은 아닙니다."])
        else:
            text.extend(["", "닫힌형 이론값 없음. CPU/GPU 일관성·표본 수렴·LT 비교 대상으로 사용합니다."])
        text.append("")
    text.extend([
        "## 반사 상한 수렴 실험", "",
        "07~09 모델에서 다른 설정을 고정하고 20 → 50 → 100 → 300 → 1,000으로 바꿉니다.",
        "총 flux·mean lux·peak·격자 데이터·도달 Ray 수·depth-limit 잔여량을 기록합니다.",
        "독립 seed 간 변동과 상한 변경 차이를 분리합니다. Peak가 안정되지 않으면 flux만 수렴했다고 보고합니다.",
        "03~05는 필요한 반사 횟수를 강제한 시험입니다. 1,000회 통로에서 상한 300일 때 0, 1,000일 때 양수인 것은 정상이며 수렴 실패가 아닙니다.", "",
        "## 내일 공유할 자료", "",
        "모델 ID·파일 SHA256, LT 버전, 광원 설정, 표면 설정, 검출면 설정, Ray 수/반사 상한/종료 조건, 총 flux/mean lux, 원본 heatmap CSV, 실행시간을 보내 주세요.",
        "사내 원본 TV 도면이나 회사 비공개 형상을 보내지 않아도 이 검증 모델로 비교할 수 있습니다.",
        "LT 일치 및 실제 TV 실측 정합성은 현재 완료 판정 대상이 아닙니다.",
    ])
    (output / "LT_SETUP_KO.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def generate(output):
    output.mkdir(parents=True, exist_ok=True)
    models, cases = definitions()
    records = []
    colors = ((0.36, 0.65, 0.83), (0.88, 0.6, 0.24), (0.45, 0.72, 0.48))
    for identifier, parts in models.items():
        assembly = cq.Assembly(name=identifier)
        components = []
        for index, (name, body) in enumerate(parts):
            shape = body.val()
            if not shape.isValid() or len(shape.Solids()) != 1 or shape.Volume() <= 0:
                raise ValueError(f"Invalid solid: {identifier}/{name}")
            assembly.add(body, name=name, color=cq.Color(*colors[index % len(colors)]))
            bounds = shape.BoundingBox()
            components.append({"name": name, "volume_mm3": shape.Volume(),
                               "bbox_min": [bounds.xmin, bounds.ymin, bounds.zmin],
                               "bbox_max": [bounds.xmax, bounds.ymax, bounds.zmax]})
        path = output / (identifier + ".stp")
        assembly.save(str(path), exportType="STEP", mode="default", write_pcurves=False)
        records.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "size_bytes": path.stat().st_size, "components": components})
    manifest = {"schema": "bitsam-lt-validation-kit.v1", "length_unit": "mm",
                "source_flux_lumen": 1, "company_geometry_used": False,
                "models": records, "cases": cases}
    (output / "models_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_setup_guide(output, cases)
    print(f"Generated {len(models)} STEP models / {len(cases)} cases: {output}", flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    generate(arguments.output)


if __name__ == "__main__":
    main()
