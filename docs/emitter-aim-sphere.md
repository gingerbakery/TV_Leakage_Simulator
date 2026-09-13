# 면광원 Aim Sphere

기존 면광원의 **발광 위치는 그대로 유지하고 초기 진행 방향의 각도 범위만 지정**한다. 점광원·체적 광원은 추가하지 않았다. CAD Surface, Datum Plane 및 기존 파일의 Reference Plane/Polygon 발광면에 적용한다.

## 사용 방법

1. Emitter properties에서 기본적으로 접힌 **Aim / Target**을 펼친다.
2. 발광 방식을 **Aim Sphere**로 선택한다. 기존 Aim Area와 동시에 사용하지 않는다.
3. Upper/Lower로 방사각 범위를, Alpha/Beta로 중심축 방향을 입력한다.
4. 보라색 각도 가이드를 확인하고 기존 Save Emitter/Add Emitter로 확정한다.

| 프리셋 | Upper / Lower | 의미 |
| --- | --- | --- |
| 전방위 — Sphere 기본값 | 0° / 180° | 앞뒤 모두, 전체 구면 4π sr |
| 반구 | 0° / 90° | 중심축의 앞쪽 반구 |
| 좁은 빔 | 0° / 15° | 중심축 반각 15°, 전체 벌어짐 30° |
| 평행광 | 0° / 0° | 모든 발광점에서 중심축과 같은 방향 |
| 사용자 입력 예 | 20° / 40° | 중심을 제외한 고리 모양 각도 영역 |

방위각은 중심축 주위 **0~360° 전체**이다. Upper/Lower는 중심축으로부터 측정한 극각이며, `0 ≤ Upper < Lower ≤ 180` 또는 평행광 `0/0`만 허용한다. 임의의 방위각 시작/끝 구간은 이번 범위에 포함하지 않는다.

### 중심축 방향

World +Z축을 World X축으로 Alpha, 다음 World Y축으로 Beta만큼 회전한다. 행렬은 `R = Ry(Beta) Rx(Alpha)`이다. Alpha=0°, Beta=90°이면 +X, Alpha=0°, Beta=180°이면 -Z를 향한다.

이 방향은 **Emitter 면의 Tilt와 독립적인 World 기준**이다. 모든 발광점에 동일한 각도 분포를 적용한다. 전방위에서는 축을 돌려도 기대 분포는 동일하지만, 유한 표본의 개별 Ray와 히트맵은 달라질 수 있다.

구·원뿔 가이드는 각도를 보여 주는 표시일 뿐이다. 가이드 크기는 발광 크기·거리·광량에 영향을 주지 않으며, 가상 구면 자체에서 Ray가 생성되지 않는다. 가이드 표시는 CAD 선택과 해석을 방해하지 않는다.

## 광학·샘플링 계약

- 계산 계약: `angular_region_uniform_solid_angle_v1`.
- 기존 면적 균일 발광점 샘플링을 유지한다. Face/Datum/Reference polygon의 위치·형상 계약은 변경하지 않는다.
- Sphere에서는 **입체각 균일** 초기 방향을 사용하며, 기존 Lambertian/Gaussian 분포는 적용하지 않는다. 기본 분포로 돌아가면 보관된 설정을 다시 사용한다.
- 극각 θ 자체를 균일하게 뽑지 않는다. 독립 균일 난수 U,V에 대해 `cosθ = cos(Upper) + U × (cos(Lower) − cos(Upper))`, `φ = 2πV`를 사용한다.
- 실제 구현은 좁은 빔에서의 수치 손실을 줄이기 위해 동등한 `sin²(θ/2)` 보간을 사용한다. 방향 벡터를 Alpha/Beta 행렬로 회전한 뒤 기존 float64 Ray batch에 전달한다.
- 초기 원점은 샘플링한 면 위 점에서 진행 방향으로 epsilon만큼 이동한다. 뒤쪽 방향도 정상 생성한다.
- Sphere는 공간상 Target 면이 없으므로 **Aim Area의 겹침/접촉 금지 조건을 적용하지 않는다.** 실제 CAD 차폐·반사 판정은 그대로 수행한다.
- CAD 표면의 뒤쪽으로 쏜 Ray는 부품 내부로 들어갈 수 있다. 다른 CAD 면에서 차폐·반사될 수 있으며, 앞뒤 발광이 CAD를 투명하게 만드는 것은 아니다.
- 첫 반사 이후 방향은 기존 표면의 Specular/Lambertian/Gaussian 계약을 따른다. 매 반사마다 Sphere 방향으로 재조준하지 않는다.

### 총광량과 SET luminance

입력 총광속 Φ를 지정한 각도 범위 전체에 배분하며, Ray 한 개의 초기 광속은 `Φ / Ray 수`이다. **전방위의 Φ는 앞+뒤 합계**이며, 양쪽 각각 Φ를 방출하여 총량이 두 배가 되지 않는다. 무차폐 전방위의 기대 앞/뒤 비율은 각각 50%이다.

Total/Power per area의 기존 총광속 계산을 유지한다. SET luminance 역시 기존 Lambertian 등가식 `Φ[lm] = π × L[nit] × A_source[mm²] × 10⁻⁶`을 유지한다. Sphere를 켰다고 2π·4π를 곱하거나 임의 nit 보정을 추가하지 않는다. 이 입력 nit는 Sphere 모드에서 모든 방향의 실제 휘도를 고정한다는 의미가 아니다.

입력 광량 전체를 선택 영역으로 집중시키므로 기본 분포와 같은 광학 조건을 보존하는 중요도 샘플링은 아니다. 좁은 각도로 제한하면 같은 총광속의 각도별 밀도가 커진다. LT·실측 비교 시 총광속 기준과 방향 분포를 별도로 맞춰야 한다.

기존 primary Receiver MIS는 Sphere에 한해 끄고 사유 `aim_sphere_overrides_receiver_mis`를 기록한다. 사용자 각도 대신 Receiver 쪽으로 Ray를 재분배하지 않기 위해서다. 후속 bounce MIS와 다른 Emitter의 MIS는 유지한다.

## 저장·호환·결과 비교

기존 `EmitterAimSpec`에 다음 필드를 추가한다. Sphere 사용 시 `mode`와 `distribution`을 함께 설정한다.

| 필드 | 기본값 / 값 |
| --- | --- |
| mode | `area` / `sphere` |
| sphere_upper_deg | 0 |
| sphere_lower_deg | 180 |
| sphere_alpha_deg | 0 |
| sphere_beta_deg | 0 |
| distribution | Area: `uniform_target_area`, Sphere: `uniform_solid_angle` |
| power_reference | `aim_region` 유지 |

- 예전 Aim 데이터에 mode가 없으면 Area로 해석한다. Aim 자체가 없으면 기존 Off 동작이다.
- Area↔Sphere 전환 시 각 모드의 좌표·크기·각도 값을 보존한다. 현재 모드에 해당하는 범위/크기를 검사하고, 사용하지 않는 모드의 값은 계산에 쓰지 않는다. 비유한 수치는 항상 거절한다.
- Alpha/Beta 입력 숫자는 직접 저장하므로 다시 편집할 때 0으로 초기화하지 않는다.
- `.bitsam` 설정 및 저장된 해석 결과에 Aim 데이터를 보존한다. CAD 포함 패키지 복원 후 기존 결과 표시와 각도 변경 후 재해석을 지원한다.
- 결과 Compare에서 Aim 방식, Upper/Lower, Alpha/Beta가 다르면 동일 조건 점수화에서 제외한다. 가이드 표시 여부는 물리 조건 비교에 포함하지 않는다.
- 이전 프로그램은 Sphere 필드를 알지 못할 수 있으므로 새 Sphere 파일은 업데이트된 프로그램에서 연다.

## 참고·검증 범위

설정 용어와 전방위/반구/평행광 예시는 로컬 LightTools 매뉴얼의 다음 항목을 참고했다.

- `D:\LightTools\Help\illumination\liug_2.3.48.html`: Aim Spheres.
- `D:\LightTools\Help\illumination\liug_2.3.49.html`: 평행광 설정.
- `D:\LightTools\Help\dbhlp\HIDC_ScatteringAimSphere_BetaOrientation.html`: Beta orientation.

LT의 광원 로컬 좌표 해석이나 Whole Region/Aim Region 광량 옵션 전체를 복제한 것은 아니다. 이번 구현은 명시적인 **World 축·입체각 균일·입력 총광량 전체 배분** 계약이다. LT 및 실측 정합성은 별도 검증 사항이다.

실행 증거와 재현 방법: `docs/changes/2026-09-10_emitter-aim-sphere.md`.
