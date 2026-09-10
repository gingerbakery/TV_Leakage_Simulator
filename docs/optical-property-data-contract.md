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

Material Library에서 `OpticalProfile`을 생성할 때의 반사율 우선순위:
1. Surface property에 `reflectance_override`가 있으면 해당 절대 반사율을 사용한다.
2. 없으면 `base.reflectance_total × surface.reflectance_scale`을 사용한다.
3. 최종값은 항상 0~1로 clamp한다.

`reflectance_override`는 미러 코팅, 고반사 필름처럼 표면 처리가 전체 반사율을 지배하는 경우에만 사용한다. 일반적인 부식, 도장, 무광/반광 처리는 기존 scale 방식을 유지한다.

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

### React 면별 관리 규칙 (2026-09-10)
- Components의 Surface Property 아이콘과 Viewer 우클릭의 Surface Property는 동일한 면별 관리창을 연다. 부품 Material 설정과 별도 진입점이다.
- 면 선택 상태에서 우클릭해도 부품 전체 선택으로 확대하지 않는다. 같은 부품의 선택 면 안에서 우클릭하면 다중 면 선택을 유지하고, 다른 면을 우클릭하면 해당 CAD 면을 선택한다.
- UI의 Face 항목은 `(component_id, mesh.face_source_ids)` 기준의 원본 CAD 면이다. 화면용 삼각형 하나를 독립 CAD 면으로 취급하지 않는다.
- ROI는 선택 가능한 화면 영역을 제한한다. 적용 시에는 해당 원본 CAD 면의 삼각형 ID 전체를 assignment에 저장한다. 같은 부품의 다른 CAD 면에는 전파하지 않는다. 기존 Material 창의 면 그룹 편집에도 같은 규칙을 사용한다.
- API의 `face_indices`에는 여전히 scene 원본 삼각형 index를 전달한다. `mesh.face_source_ids`의 CAD topology ID와 혼용하지 않으며, Viewer/Trace mesh 사이의 기존 bridge 매핑을 유지한다.
- ROI 절단면(cap)은 표시용 가상 면이며 물성·Emitter 대상에 추가하지 않는다. ROI 밖의 원본 면은 목록에 남기되 선택을 비활성화한다.
- Face Override의 Base Material은 해석 요청을 만들 때 현재 활성 Part Assignment에서 상속한다. Part Assignment가 없으면 face assignment의 저장된 base를 사용한다. Surface Property는 해당 면의 지정값을 유지한다.
- 활성 Face Override를 새로 적용하면 같은 부품의 이전 face assignment에서 중복 ID를 제거한다. `Use part default`는 선택 면의 override만 제거하며, 다른 면과 Part Assignment는 유지한다.
- `.bitsam`은 기존 `materialAssignments` 형식으로 저장한다. 좌표형 ROI의 `clipBox.plane = xyz`도 유효한 저장 형식이다.

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
- `faceColorOverrides`와 `componentColorOverrides`는 시각적 표시 전용이다. 반사율/산란 모델/재료 assignment와 독립적이며 광학 요청에 전송하지 않는다. 표시색 변경 및 초기화는 완료된 해석 결과를 무효화하지 않는다.
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
