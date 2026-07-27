# Ray Tracing 기여도 데이터 계약

## 목적
- RT-2D 결과에서 직접광과 반사광을 분리한다.
- 어떤 Receiver, 부품, 면, 소재, 반사 lobe가 빛샘 결과에 기여했는지 동일한 형식으로 집계한다.
- UI, 리포트, CSV 내보내기가 ray tracing 내부 구현에 직접 의존하지 않도록 안정적인 결과 계약을 제공한다.

## 스키마
- 스키마 버전: `rt-contribution.v1`
- 정식 위치: `RayTraceResult.contribution_summary`
- 호환 위치: `RayTraceResult.metrics._contribution_summary`
- 두 위치의 값은 동일하며, 신규 코드는 정식 위치를 우선 사용한다.

## 최상위 구조

| 필드 | 의미 |
| --- | --- |
| `direct_receiver_hit_count` | 반사 없이 Receiver에 도달한 ray 수 |
| `direct_receiver_flux_lumen` | 직접광으로 Receiver에 도달한 광속 합계 |
| `reflected_receiver_hit_count` | 1회 반사 후 Receiver에 도달한 ray 수 |
| `reflected_receiver_flux_lumen` | 1회 반사 후 Receiver에 도달한 광속 합계 |
| `receivers` | Receiver별 직접광·반사광·총 기여도 |
| `components` | 부품별 입사·반사·차폐·Receiver 기여도 |
| `faces` | CAD triangle face별 상세 기여도 |
| `materials` | optical material/profile별 기여도 |
| `lobes` | `specular`, `lambertian`, `gaussian`별 기여도 |
| `depths` | 직접광과 각 반사 깊이별 surface·Receiver·종료 기여도 |

## Receiver 기여도
- `direct`: 직접 도달 ray의 `hit_count`, `flux_lumen`
- `reflected`: 반사 도달 ray의 `hit_count`, `flux_lumen`
- `total`: 직접광과 반사광의 합계
- `lobes`: 반사광을 반사 모델별로 분해한 값

Receiver 항목은 활성 Receiver마다 항상 생성한다. 도달 ray가 없으면 모든 값은 0이다.
Receiver의 `depths`는 `0=직접광`, `1=1회 반사`, `2=2회 반사` 방식으로 hit와 flux를 분리한다.

## Surface 기여도
`faces`, `components`, `materials`는 동일한 집계 필드를 사용한다.

| 필드 | 의미 |
| --- | --- |
| `primary_hit_count` | Emitter에서 출발한 ray가 해당 surface에 최초 충돌한 횟수 |
| `incident_flux_lumen` | 최초 충돌 시 입사 광속 |
| `reflectable_flux_lumen` | 반사율 적용 후 반사 가능한 광속 예산 |
| `reflection_emitted_count` | 해당 surface에서 실제 반사 ray를 생성한 횟수 |
| `reflection_emitted_flux_lumen` | 생성한 반사 ray의 광속 |
| `receiver_hit_count` | 해당 surface에서 반사된 뒤 Receiver에 도달한 횟수 |
| `receiver_flux_lumen` | 해당 surface에서 반사되어 Receiver에 도달한 광속 |
| `reflection_blocked_count` | 해당 surface에서 반사된 ray가 다른 CAD surface에 차단된 횟수 |
| `reflection_blocked_flux_lumen` | 차단된 반사 ray의 광속 |
| `secondary_block_count` | 다른 surface에서 반사된 ray를 이 surface가 차단한 횟수 |
| `secondary_blocked_flux_lumen` | 이 surface가 차단한 반사 광속 |
| `escaped_count` | 반사 후 Receiver와 CAD에 닿지 않고 이탈한 횟수 |
| `escaped_flux_lumen` | 이탈한 반사 광속 |
| `lobes` | 위 결과를 반사 모델별로 분리한 값 |

`reflection_blocked_*`는 반사 발생 면의 결과이고, `secondary_block_*`는 실제 차폐 면의 기여도다. 두 값을 구분해야 반사 원인 부품과 차폐 부품을 각각 찾을 수 있다.

## 집계 규칙
1. ray loop에서는 실제로 충돌한 face만 희소 집계한다.
2. component와 material 기여도는 tracing 완료 후 face 집계에서 한 번만 합산한다.
3. face의 component ID는 `component_id`, 없으면 `step_component_id`, 모두 없으면 `unassigned`를 사용한다.
4. material ID가 없으면 `unassigned`를 사용한다.
5. 광속 단위는 모두 lumen이며 음수 값은 허용하지 않는다.
6. hit count는 ray 사건 수이며 Receiver pixel 수나 CAD face 수가 아니다.

## 집계 모드
- `summary`: 빠른 반복 계산용이다. Receiver, direct/reflected, lobe와 기본 depth 결과를 유지하고 `components`, `faces`, `materials`는 비워 둔다.
- `detailed`: 원인 분석용이다. Component, face, material과 surface depth 기여도를 모두 계산한다.
- 두 모드는 ray 방향, 반사율 감쇄, CAD 교차 및 Receiver 광속 계산이 동일하다.
- 집계 모드는 `RayTraceConfig.contribution_mode`에 기록한다.

## 보존 관계
- `receiver.total.flux_lumen = receiver.direct.flux_lumen + receiver.reflected.flux_lumen`
- 전체 직접광 합계는 모든 Receiver의 direct flux 합계와 같아야 한다.
- 전체 반사광 합계는 모든 Receiver의 reflected flux 합계와 같아야 한다.
- component/material 집계는 해당 그룹에 포함된 face 집계의 합과 같아야 한다.
- 반사 면의 `reflection_emitted_flux_lumen`은 Receiver 도달, 차단, 이탈 결과로 분해할 수 있어야 한다.

## 현재 범위와 다음 단계
- 현재 계약은 RT-3의 직접광과 `max_depth=0~20` 다회 반사를 집계한다.
- RT-2D-B에서는 이 계약을 이용해 UI 기여도 표, 정렬, 필터, CSV/리포트를 구현한다.
- RT-3에서 `depths` 집계를 추가했으며 `rt-contribution.v1`의 기존 필드는 전체 bounce 누적 합계로 유지한다.
