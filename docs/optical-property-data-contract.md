# Optical Property 데이터 계약

## 목적
CAD, Material Library, Ray Tracing 개발자가 동일한 표면 광학 정보를 사용하도록 입력·출력 형식과 조회 우선순위를 고정한다.

## 범위
- RT-2B: 최초 CAD surface 충돌에서 최종 optical profile을 조회하고 반사 가능 광량을 계산한다.
- RT-2C: RT-2B 결과를 이용해 실제 정반사/산란 ray 방향을 생성한다.
- 투과, 굴절, 회절, 간섭은 V1 범위에서 제외한다.

## 입력 계약

### `OpticalProfile`

| 필드 | 형식 | 의미 |
|---|---:|---|
| `profile_id` | string | profile 고유 ID |
| `reflectance` | float, 0~1 | 입사 광량 중 반사 가능한 총 비율 |
| `absorption` | float | 호환 필드. 입력값과 무관하게 `1 - reflectance`로 계산 |
| `specular_ratio` | float | 반사광 중 정반사 성분 비율 |
| `diffuse_ratio` | float | 반사광 중 확산/산란 성분 비율 |
| `scatter_model` | enum | `none`, `specular`, `lambertian`, `gaussian`, `mixed` |
| `roughness` | float, 0~1 | 표면 거칠기 보조값 |
| `gaussian_sigma_deg` | float | Gaussian lobe 각도 폭 |
| `bsdf_asset_id` | string/null | 측정 BSDF 연결 ID |

규칙:
- `reflectance`는 0~1로 clamp한다.
- `specular_ratio`와 `diffuse_ratio`는 합이 1이 되도록 정규화한다.
- 두 비율이 모두 0이면 반사 방향 ray를 생성하지 않는다.
- `scatter_model = mixed`에서는 `specular_ratio` 확률로 Gaussian glossy lobe를 선택하고, `diffuse_ratio` 확률로 Lambertian lobe를 선택한다.
- 완전 경면은 `scatter_model = specular`, 단일 glossy 분포는 `scatter_model = gaussian`을 사용한다.

### `OpticalAssignment`

| 필드 | 형식 | 의미 |
|---|---:|---|
| `assignment_id` | string | assignment 고유 ID |
| `target_type` | `part`/`faces` | component 전체 또는 face override |
| `component_id` | integer | CAD component ID |
| `profile_id` | string | 적용할 profile ID |
| `face_indices` | integer[] | `faces`일 때 source face ID 목록 |
| `priority` | integer | 같은 대상 내 충돌 우선순위 |
| `enabled` | boolean | 활성 상태 |

호환 alias:
- `component` → `part`
- `face_override` → `faces`
- `object_id` → `component_id`

## 조회 규칙
1. `(component_id, source_face_index)`와 일치하는 Face Override
2. `component_id`와 일치하는 Part Assignment
3. CAD mesh face의 `material_id`와 같은 `profile_id`
4. `profile_id = default`
5. `__unassigned_absorber__`: 반사율 0의 안전 종료 profile

Assignment가 존재하지만 해당 `profile_id`가 전달되지 않았으면 그 assignment는 건너뛰고 다음 fallback을 조회한다.

## 충돌 이벤트 출력 계약
Surface `RayHit`에는 다음 항목을 기록한다.

- `optical_profile_id`
- `reflectance`
- `scatter_model`
- `optical_assignment_source`
- `ray_kind`: `direct`, `specular`, `gaussian`, `lambertian`
- `incoming_energy_lumen`
- `outgoing_energy_lumen`

RT-2B에서:

```text
outgoing_energy_lumen = incoming_energy_lumen * reflectance
terminated_energy_lumen = incoming_energy_lumen - outgoing_energy_lumen
```

`outgoing_energy_lumen`은 실제로 다음 ray가 발사되었다는 뜻이 아니라 RT-2C가 사용할 반사 에너지 예산이다.

RT-2C에서는 이 에너지 예산을 중복 감쇄 없이 그대로 반사 ray에 전달한다.

```text
Emitter event → first surface event → Receiver 또는 secondary surface event
```

- direct path는 이벤트 2개다.
- 1회 반사 path는 이벤트 3개다.
- secondary surface event는 `depth=1`, `outgoing_energy_lumen=0`으로 기록한다.

## 결과 요약 계약
`RayTraceResult.metrics._optical_summary`:

- `surface_hit_count`
- `unassigned_surface_hit_count`
- `profile_hits[profile_id].hit_count`
- `profile_hits[profile_id].source`
- `profile_hits[profile_id].reflectance`
- `profile_hits[profile_id].specular_ratio`
- `profile_hits[profile_id].diffuse_ratio`
- `profile_hits[profile_id].scatter_model`
- `profile_hits[profile_id].incoming_flux_lumen`
- `profile_hits[profile_id].potential_reflected_flux_lumen`

`RayTraceResult.metrics._reflection_summary`:

- `direct_receiver_hit_count`
- `direct_receiver_flux_lumen`
- `reflection_emitted_count`
- `reflection_receiver_hit_count`
- `reflected_receiver_flux_lumen`
- `reflection_blocked_count`
- `reflection_escaped_count`
- `lobes[specular|gaussian|lambertian]`

`unassigned_surface_hit_count > 0`이면 분석 결과에 미지정 optical property 경고를 표시해야 한다.

## 개발 경계
- CAD 담당: `component_id`, `source_face_index`, `material_id`를 안정적으로 유지한다.
- Material 담당: profile/assignment를 생성하고 ID 참조 무결성을 유지한다.
- Ray Tracing 담당: 조회 우선순위를 변경하지 않고 energy conservation을 보장한다.
- UI 담당: 반사율은 총 반사율로 표시하고 독립 흡수율 입력을 제공하지 않는다.
- RT-2C 담당: 반사광 전체에 reflectance를 다시 곱하지 않는다. RT-2B의 `outgoing_energy_lumen`을 시작 광량으로 사용한다.

## 회귀 검증
- Face Override가 Part Assignment보다 우선한다.
- Part Assignment가 CAD material fallback보다 우선한다.
- default와 unassigned fallback이 결정적으로 동작한다.
- 입력 1 lumen, 반사율 R에서 반사 가능 광량이 R lumen이다.
- 반사 가능 광량과 종료 광량의 합이 입사 광량과 같다.
