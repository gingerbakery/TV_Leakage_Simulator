# Ray Path 기반 빛샘 완화 자동화 설계

## 문서 상태

- 상태: 설계 제안
- 작성일: 2026-10-23
- 구현 범위: 이 문서는 데이터 계약, 사용자 흐름, 검증 기준을 정의한다. 제품 코드는 포함하지 않는다.
- 1차 목표: 사용자가 선택한 Receiver 영역과 설계 가능 면을 바탕으로 차광 구조를 **제안**하고, 원본 Case를 변경하지 않은 Candidate Case에서 재해석하여 효과를 검증한다.
- 장기 목표: 다중 후보 최적화, 기구 제약 검증, 독립 CAD Feature 내보내기까지 단계적으로 확장한다.

## 1. 결론

현재 코드에는 Receiver Heatmap 영역 선택, 저장된 Ray Path의 진단용 기여도 분석, Preview 차광 Box 생성, Precision Ray Tracing에 임시 차광 형상을 포함하는 기반이 이미 있다. 따라서 추천형 자동화는 구현 가능하다.

다만 현재 결과만으로 “주요 경로를 정확히 찾아 최적 구조를 자동 생성했다”고 말할 수는 없다. 저장된 Ray Path는 표시용으로 수가 제한되고 Receiver 도달 경로를 우선 보존하는 편향 표본이며, Receiver Cell별 전체 경로 귀속과 순서가 보존되지 않는다. 또한 Preview와 Precision에서 임시 차광물의 광학 특성이 달라질 수 있다. 기본 Leak Preview 설정·허용 영역·차광 구조는 이제 Case별 프로젝트에 저장되지만, 자동 제안·경로 귀속·Baseline/Candidate 검증 이력을 연결하는 provenance 계약은 아직 없다.

따라서 다음 원칙을 적용한다.

1. P0 차단 이슈를 해결하기 전에는 자동 제안을 제품 기능으로 활성화하지 않는다.
2. 1차 출시는 원본 CAD를 수정하지 않는 추천 전용 기능으로 한정한다.
3. 사용자가 Receiver 영역과 Host Face 또는 설계 허용 영역을 명시해야 제안을 생성한다.
4. 모든 Candidate는 원본 Baseline과 분리해 생성하고, 동일 조건 재해석으로 검증한다.
5. 데이터가 부족하거나 비교 조건이 다르면 “개선됨”으로 판정하지 않고 이유를 표시한다.
6. 광학 성능 검증과 기구 설계 가능성 검증을 서로 다른 상태로 관리한다.

## 2. 현재 기능과 근거

| 현재 기능 | 확인된 근거 | 재사용 범위 | 한계 |
|---|---|---|---|
| Receiver Heatmap 영역 선택 및 영역 Flux 계산 | `frontend/src/features/results/result-window.tsx`의 `regionAnalysis` | 사용자가 문제 영역을 직접 지정하는 시작점 | 영역별 경로 귀속은 `stored_paths` 표본에 의존 |
| 선택 영역의 Component, Face, Lobe, Depth, 경로 순서 분석 | `result-window.tsx`의 `matchingPaths` 및 기여도 집계 | 원인 탐색 UI와 3D 경로 강조 | 제한·편향 표본이므로 정량 전체 기여도로 사용 불가 |
| 전체 해석 기여도 집계 | `RayTraceContributionSummary`의 Receiver, Component, Face, Material, Lobe, Depth 집계 | 전체 Receiver 기준의 정량 요약 | Receiver Cell별 또는 순서가 있는 Route별 집계가 아님 |
| Ray Event 기록 | Python/TypeScript `RayHit` | Face, Component, Material, 위치, 법선, 에너지, Depth, Event, Receiver, 광학 프로파일, Lobe 기록 | Emitter ID, 안정적인 Path ID, Source Face ID, 탈출 개구 Event가 계약에 없음 |
| Receiver Flux 및 제곱합 Grid | `ReceiverGrid.flux_lumen`, `flux_squared_lumen2_grid` | 영역 Flux와 Monte Carlo 오차 추정 | Direct/Reflected 및 Route별 제곱합은 없음 |
| Preview 빛샘 후보 탐지 | `detectLeakPreviewCandidates`와 `LeakPreviewCandidate` | Hotspot 후보, 방향, 범위, 표본 경로 특징 재사용 | 최종 정량 해석이 아닌 표시 Tessellation 기반 위험 위치 검색 |
| 선택 Face 기반 차광 Box 생성 | `createLeakPreviewBlockerFromFaces` | Host Face 기준 초기 형상 생성 | 직육면체 하나만 지원하며 성형·간섭 제약이 없음 |
| Preview/Precision 임시 차광 형상 | `buildLeakPreviewRequest`, `buildPrecisionPreviewConstraints`, `_append_preview_blockers` | Candidate 형상을 실제 Ray 교차 대상으로 사용 | 광학 특성 불일치와 Blocker ID 추적 한계가 있음 |
| Case 결과 비교 | Result Window의 Analysis Case와 Peak/Flux/Light-area 비교 | Baseline/Candidate 비교 UI 기반 | 동일 조건 여부와 통계적 유의성 게이트가 부족 |
| 단일 Case 휴대형 V2 및 다중 Case Workspace | `docs/bitsam-portable-v2.md` 및 Workspace 패키지 구현 | 원본 CAD, 형상 캐시, 설정, 마지막 결과, Preview 설정·허용 영역·차광 구조 복원 | 자동 제안, Attribution 출처, Baseline/Candidate Run 연결 이력은 저장 계약에 없음 |
| Geometry 캐시 | `runtime.py::_trace_geometry_cache_key` | 동일 입력의 Trace Geometry 재사용 | `preview_blockers` 전체가 키에 포함되어 후보 치수 변경마다 전체 Geometry/BVH 재구성 |

## 3. P0 차단 이슈

### P0-1. Preview와 Precision의 차광물 광학 특성 불일치

현재 흐름은 같은 차광 Box가 서로 다른 광학 특성으로 계산될 수 있다.

- Preview 요청은 `buildLeakPreviewRequest`에서 `profile_id = "default"`, 반사율 `0.35`인 중립 Fallback을 추가한다.
- 차광 Box Triangle은 `_append_preview_blockers`에서 Material ID `__auxiliary_absorber__`로 추가되지만 별도의 명시적 광학 Assignment는 없다.
- `OpticalPropertyResolver`는 Mesh Material에 해당하는 프로파일이 없으면 `default` 프로파일을 사용하고, 그것도 없을 때만 안전한 미지정 흡수체를 사용한다.
- 따라서 Preview에서는 차광물이 35% 반사체가 될 수 있고, 일반적인 Precision 요청에서는 `default`가 없으므로 0% 반사 미지정 흡수체가 될 수 있다.

이는 Preview 후보 순위와 Precision 재해석 결과를 직접 비교할 수 없게 만드는 P0 오류다.

해결 계약:

1. 모든 임시 차광물은 `blocker_optical_profile_id`를 명시한다.
2. Preview와 Precision은 동일한 프로파일 ID와 동일한 수치 파라미터를 사용한다.
3. 초기 기본값은 명시적인 `mitigation_absorber.v1`로 두며, Catch-all `default` 존재 여부에 의존하지 않는다.
4. 사용자가 프로파일을 바꾸면 제안, Candidate Case, 저장 프로젝트에 함께 기록한다.
5. Preview와 Precision의 동일 단순 Scene에서 차광물 입사·반사·흡수 결과가 허용 오차 내 일치해야 한다.

### P0-2. 자동 제안 및 검증 provenance 저장 계약 미완성

현재 Case별 Leak Preview Source, 품질, 방향, 표시 상태, Allowed Area, Blocker는 단일 Case V2와 다중 Case Workspace에 저장·복원된다. 동일 Geometry의 Scene Token만 갱신되면 상태를 유지하고, 실제 CAD 또는 Accessory 구성이 달라지면 잘못된 Face 연결을 피하기 위해 초기화한다.

그러나 향후 자동화가 생성할 Suggestion, 정량 Attribution 출처, Baseline/Candidate Run ID, 제안 승인 상태와 통계적 검증 결과를 하나로 묶는 저장 계약은 아직 없다. 이 계약 없이 자동 제안을 활성화하면 Candidate 해석의 근거와 실제 형상이 분리될 수 있다.

해결 계약:

1. 기존 Case별 Leak Preview 상태를 하위 호환 입력으로 유지하면서 `mitigation_workspace` 계약을 추가한다.
2. Host Face, Allowed Design Zone, Blocker와 함께 Suggestion, Attribution 출처, Baseline/Candidate Run ID를 저장한다.
3. Scene Hash 또는 Face 매핑이 달라지면 자동 재연결하지 않고 `stale`로 표시한다.
4. 이전 프로젝트에는 빈 `mitigation_workspace`를 적용하되, 저장된 결과에서 제안을 추측 생성하지 않는다.
5. 저장·복원 Round-trip 후 시각 형상, 광학 프로파일, 활성 상태, 검증 결과가 동일해야 한다.

### P0-3. 저장 Ray Path의 정량 오용 위험

`_store_completed_path`는 설정된 최대 개수까지만 Path를 저장한다. 저장소가 가득 찬 뒤 Receiver 도달 경로는 오래된 Dead-end를 교체하지만, Receiver 도달 경로끼리는 Flux 비례 Reservoir Sampling을 하지 않는다. 이 컬렉션은 코드 주석대로 표시 및 진단 전용이다.

따라서 기존 `stored_paths`로 계산한 Component 비율, Route 순위, 반사 횟수 분포에는 다음 제한이 있다.

- 모집단 포함 확률이 알려져 있지 않다.
- 앞서 완료된 Path와 Receiver 도달 Path가 과대표집될 수 있다.
- Path 수를 늘려도 무편향 추정치가 된다고 보장할 수 없다.
- 선택 영역의 정확한 Flux는 Receiver Grid에서 얻을 수 있지만 그 Flux의 경로별 원인을 정확히 분해할 수 없다.

해결 전에는 `stored_paths` 기반 결과에 항상 `diagnostic_sample` 라벨을 붙이고, 자동 치수 최적화 및 “주요 원인 확정”에 사용하지 않는다.

## 4. 데이터 신뢰도 및 Fail-closed 라벨

각 분석 결과와 제안은 아래 신뢰도 중 하나를 가져야 한다.

| 라벨 | 의미 | 허용 동작 |
|---|---|---|
| `exact_totals` | 전체 Ray를 온라인 집계한 Receiver/Cell 총량 | 정량 지표와 비교 기준으로 사용 |
| `bounded_routes` | 총량은 정확하고 Route Top-K와 Tail 총량을 함께 보존 | 포착률이 Gate 이상이면 추천 근거로 사용 |
| `diagnostic_sample` | 기존 `stored_paths` 또는 포함 확률을 모르는 제한 표본 | 화면 강조와 탐색 힌트만 허용 |
| `insufficient` | 경로 수, Flux, 포착률, Face 매핑 또는 오차 정보 부족 | 제안 생성과 개선 판정 비활성화 |
| `stale` | Scene/Geometry/설정 Hash가 분석 시점과 다름 | 재해석 전까지 제안 적용 금지 |

Fail-closed 이유 코드는 최소한 다음을 지원한다.

- `legacy_sample_only`
- `route_flux_coverage_below_gate`
- `receiver_region_flux_below_gate`
- `scene_signature_mismatch`
- `host_face_missing`
- `host_face_mapping_ambiguous`
- `blocker_optics_mismatch`
- `mechanical_constraints_missing`
- `collision_or_clearance_failed`
- `comparison_configuration_mismatch`
- `uncertainty_too_high`
- `baseline_or_candidate_incomplete`

이 라벨과 차단 사유는 안전한 사용에 필요한 운영 정보이므로 화면에 직접 표시한다. 긴 의미 설명은 해당 제목 옆 `HelpTooltip`의 `(?)`에 둔다.

## 5. 목표 사용자 흐름

### 5.1 단계 1 — Receiver 문제 영역 선택

1. Result의 Receiver Heatmap에서 사용자가 사각형 또는 Cell 집합을 선택한다.
2. 선택 영역의 면적, Flux, 총 Receiver Flux 대비 비율, Peak/Mean nit, Monte Carlo 오차를 표시한다.
3. 선택 영역은 Receiver ID, Grid 해상도, Cell 범위, 정규화 UV 범위, 선택 당시 Run ID와 함께 저장한다.
4. 정확한 경로 귀속 데이터가 없으면 “진단 표본만 사용 가능”을 표시하고 자동 제안 버튼을 비활성화한다.

### 5.2 단계 2 — 원인 Route 확인

1. `path_attribution.v1`에서 선택 Cell들의 정확 총량과 Route Top-K를 합산한다.
2. Direct, 1-bounce, 2+-bounce를 분리한다.
3. Emitter, Component, Source Face, Material, Lobe, 순서가 있는 Route를 Flux 순으로 표시한다.
4. Top-K 밖 Tail Flux와 Route 포착률을 함께 표시한다.
5. 사용자가 한 Route를 선택하면 대표 Terminal Segment와 관련 Surface를 3D Viewer에서 강조한다.

### 5.3 단계 3 — Host Face 및 설계 허용 영역 지정

초기 버전은 Host Face를 자동 확정하지 않는다.

1. 사용자가 3D Viewer에서 차광 구조를 부착할 Component Face를 선택한다.
2. 필요하면 Face 내부의 설계 허용 영역을 Box 또는 Polygon으로 제한한다.
3. 보호 개구, Receiver, Emitter, 이동 부품, 체결부 등 금지 영역을 지정한다.
4. Face는 일시적인 Trace Triangle 번호가 아니라 `component_id + source_face_id + scene_signature`로 참조한다.
5. Face가 재매핑되지 않거나 여러 Face로 모호하게 매핑되면 제안을 생성하지 않는다.

### 5.4 단계 4 — Draft Blocker 제안

1. 선택 Route의 Terminal Segment와 Host Face 교차를 Host 좌표계로 투영한다.
2. Flux 가중 Segment 분포의 분위수 범위로 초기 중심, 폭, 높이를 구한다.
3. 최소 두께와 간극을 적용해 `blocker_box_v1` Draft를 만든다.
4. 초기에 1~3개 후보만 제시하며, 각 후보에 예상 차단 Flux, Route 포착률, 신뢰도, 제약 상태를 표시한다.
5. Draft는 반투명 Overlay이며 원본 CAD나 Baseline Case를 변경하지 않는다.
6. 사용자가 치수를 수정하거나 명시적으로 “Candidate 생성”을 누르기 전에는 Ray Tracing 입력에 포함하지 않는다.

### 5.5 단계 5 — Baseline/Candidate 재해석

1. Baseline은 변경 없이 고정한다.
2. Candidate는 Baseline 설정을 복제한 뒤 선택한 Draft Blocker만 추가한다.
3. Baseline 결과가 현재 설정과 동일한 서명으로 이미 완료되어 있으면 재사용한다.
4. Candidate는 동일 Ray 수, Emitter, Receiver, 광학 프로파일, 난수 Seed, 가속 구조 설정으로 순차 실행한다.
5. 현 Runtime은 새 작업이 활성 작업을 중단·대체할 수 있으므로, 전용 Queue가 생기기 전까지 동시에 실행하지 않는다.
6. 중지, 오류, 앱 재시작 시 Candidate 상태를 `queued`, `running`, `failed`, `cancelled`, `completed`로 보존한다.

### 5.6 단계 6 — 검증된 비교와 채택

1. 선택 영역과 전체 Receiver의 절대 지표를 Baseline/Candidate로 나란히 표시한다.
2. 동일 조건, 완전한 실행, 통계 오차 Gate, 광학 프로파일 일치가 모두 확인되어야 `validated_improvement`를 표시한다.
3. 선택 영역이 좋아졌더라도 다른 Receiver 또는 보호 영역이 악화되면 경고하고 자동 채택하지 않는다.
4. 사용자가 채택하면 Suggestion 상태만 `accepted`로 바꾸고 Case에 저장한다.
5. 원본 CAD 편집 또는 CAD 내보내기는 별도 명시 동작으로 둔다.

## 6. 제안 데이터 계약: `path_attribution.v1`

`path_attribution.v1`은 전체 Ray 흐름 중 Receiver Cell에 도달한 기여를 Trace 중 온라인으로 집계한다. 기존 `stored_paths`를 변환해 이 스키마를 “정확”으로 표시해서는 안 된다.

아래는 제안 구조다. 실제 직렬화는 대용량 배열을 Binary Sidecar로 분리할 수 있지만 의미 계약은 유지한다.

```jsonc
{
  "schema_version": "path_attribution.v1",
  "run_id": "run-uuid",
  "scene_signature": "sha256:...",
  "trace_request_signature": "sha256:...",
  "aggregation": {
    "mode": "online_exact_totals_bounded_routes",
    "top_k_routes_per_cell": 16,
    "terminal_segment_sampler": "weighted_priority_reservoir_v1",
    "terminal_segment_limit_per_cell": 32,
    "sampling_seed": 12345
  },
  "quality": {
    "trust_label": "bounded_routes",
    "receiver_flux_lumen": 0.0123,
    "attributed_flux_lumen": 0.0118,
    "tail_flux_lumen": 0.0005,
    "route_flux_coverage": 0.9593,
    "receiver_hit_population": 182034,
    "terminal_segment_sample_count": 4096,
    "warnings": []
  },
  "route_catalog": {
    "route:sha256:...": {
      "emitter_id": "emitter-1",
      "path_kind": "reflected",
      "reflection_count": 2,
      "steps": [
        {
          "depth": 0,
          "component_id": 18,
          "source_face_id": 731,
          "material_id": "black_pc",
          "optical_profile_id": "black_pc_matte",
          "lobe": "diffuse"
        }
      ]
    }
  },
  "receivers": [
    {
      "receiver_id": "receiver-1",
      "resolution": [128, 128],
      "cells": [
        {
          "column": 54,
          "row": 21,
          "totals": {
            "hit_count": 245,
            "flux_lumen": 0.000012,
            "flux_squared_lumen2": 1.7e-12,
            "direct_flux_lumen": 0.000002,
            "reflected_flux_lumen": 0.000010
          },
          "top_routes": [
            {
              "route_id": "route:sha256:...",
              "hit_count": 73,
              "flux_lumen": 0.000007,
              "flux_squared_lumen2": 8.1e-13
            }
          ],
          "tail": {
            "route_count": 9,
            "hit_count": 172,
            "flux_lumen": 0.000005,
            "flux_squared_lumen2": 8.9e-13
          },
          "terminal_segments": [
            {
              "sample_id": "segment:...",
              "path_id": "path:...",
              "route_id": "route:sha256:...",
              "emitter_id": "emitter-1",
              "origin_mm": [1.0, 2.0, 3.0],
              "receiver_point_mm": [4.0, 5.0, 6.0],
              "direction": [0.1, 0.2, 0.97],
              "flux_lumen": 4.2e-8,
              "inclusion_probability": 0.13
            }
          ]
        }
      ]
    }
  ]
}
```

### 6.1 필수 집계 규칙

1. Receiver Cell의 `hit_count`, `flux_lumen`, `flux_squared_lumen2`, Direct/Reflected Flux는 모든 Ray를 대상으로 온라인 집계한다.
2. Route Key는 `emitter_id`와 순서가 있는 Surface Step으로 생성한다. Direct Path는 Step이 0개인 유효 Route다.
3. Stable Face 참조는 `source_face_id`를 사용한다. `trace_face_index`는 선택적 진단 필드일 뿐 저장 후 식별자에 사용하지 않는다.
4. Top-K는 Space-Saving 또는 동등한 Bounded Heavy-hitter 알고리즘으로 유지하고, 제외된 모든 기여를 `tail`에 보존한다.
5. `sum(top_routes.flux_lumen) + tail.flux_lumen`은 Cell 총 Flux와 수치 허용 오차 내 일치해야 한다.
6. Terminal Segment는 포함 확률을 계산할 수 있는 가중 Reservoir 방식으로 표본화한다. 기존 Receiver 우선 `stored_paths`를 이 표본으로 재사용하지 않는다.
7. Emitter ID, Path ID, Sampling Seed를 기록하여 동일 Seed 비교와 추적이 가능해야 한다.
8. 저장 용량이 Gate를 넘으면 Route K 또는 Segment 수를 줄일 수 있으나 총량과 Tail은 삭제하지 않는다.

### 6.2 레거시 결과

`path_attribution.v1`이 없는 기존 결과는 다음과 같이 다룬다.

- Receiver Grid 지표는 `exact_totals`로 유지한다.
- Component/Face/Sequence 분석은 `diagnostic_sample`로 표시한다.
- 자동 Draft 위치 계산과 자동 최적화는 비활성화한다.
- 사용자가 수동으로 Host Face와 Blocker를 만들고 재해석하는 흐름은 허용한다.

## 7. 제안 데이터 계약: `MitigationSuggestion`

`MitigationSuggestion`은 분석 근거, 임시 형상, 제약 조건, 검증 결과를 하나의 Case 종속 레코드로 묶는다.

```ts
interface MitigationSuggestion {
  schema_version: 'mitigation-suggestion.v1'
  suggestion_id: string
  case_id: string
  label: string
  status:
    | 'draft'
    | 'accepted'
    | 'queued'
    | 'validating'
    | 'validated'
    | 'rejected'
    | 'stale'

  source: {
    baseline_run_id: string
    scene_signature: string
    trace_request_signature: string
    attribution_schema: 'path_attribution.v1'
    attribution_trust: 'exact_totals' | 'bounded_routes' | 'diagnostic_sample' | 'insufficient'
  }

  target: {
    receiver_id: string
    region_id: string
    cell_bounds: { column_min: number; column_max: number; row_min: number; row_max: number }
    normalized_uv_bounds: { u_min: number; u_max: number; v_min: number; v_max: number }
    baseline_flux_lumen: number
  }

  cause: {
    route_ids: string[]
    captured_route_flux_ratio: number
    dominant_emitter_ids: string[]
    dominant_component_ids: number[]
    dominant_source_face_ids: number[]
    evidence_warnings: string[]
  }

  host: {
    component_id: number
    source_face_ids: number[]
    scene_signature: string
    origin_mm: [number, number, number]
    u_axis: [number, number, number]
    v_axis: [number, number, number]
    normal: [number, number, number]
    allowed_zone_id?: string
  }

  geometry: {
    kind: 'blocker_box_v1'
    base_center_uv_mm: [number, number]
    offset_mm: number
    width_mm: number
    height_mm: number
    thickness_mm: number
    reverse_normal: boolean
    search_ranges?: Record<string, { min: number; max: number; step: number }>
  }

  optics: {
    profile_id: string
    profile_signature: string
  }

  constraints: {
    constraint_set_id: string
    provenance: 'user' | 'cad_derived' | 'mixed'
    feasibility: 'unverified' | 'mesh_screened' | 'cad_verified'
    violations: Array<{ code: string; severity: 'warning' | 'blocking'; value?: number }>
  }

  prediction: {
    method: 'segment_interception_estimate_v1' | 'coarse_retrace_v1'
    predicted_blocked_flux_lumen: number
    confidence: 'low' | 'medium' | 'high'
    limitations: string[]
  }

  validation?: {
    baseline_run_id: string
    candidate_run_id: string
    comparison_signature: string
    metrics: MitigationComparisonMetrics
    uncertainty: MitigationUncertainty
    outcome: 'improved' | 'inconclusive' | 'regressed' | 'invalid'
  }

  created_at: string
  updated_at: string
}
```

추가 규칙:

1. Suggestion은 특정 Case와 Scene Signature에 속한다.
2. Blocker 순서로 생성되는 음수 Component ID를 영구 식별자로 사용하지 않는다.
3. Geometry/Optics/Constraint가 바뀌면 기존 Validation을 삭제하지 않고 `stale`로 전환해 이력을 보존한다.
4. `validated`는 광학 비교가 완료되었다는 뜻이며 `cad_verified` 또는 금형 승인이라는 뜻이 아니다.
5. Candidate Case 삭제 시 Suggestion 이력은 사용자가 함께 삭제할지 확인한다.

## 8. 평가 지표와 불확실성

### 8.1 필수 광학 지표

Baseline과 Candidate에 대해 다음을 동일 단위로 기록한다.

- 선택 Receiver 영역 Flux, Peak nit, Mean nit
- 선택 영역의 전체 Receiver Flux 대비 비율
- 전체 Receiver Flux, Peak nit, Mean nit
- 고정 절대 Threshold 이상 면적과 비율
- Direct Flux와 Reflected Flux
- 1-bounce와 2+-bounce Flux
- 보호 Receiver/영역의 Flux 및 Peak 변화
- Ray 수, Seed, 실행 시간, Geometry/BVH 캐시 상태

상대 최대값의 1%, 5%, 10% 면적은 분포 형태 비교용 보조 지표로만 사용한다. Candidate의 Peak 자체가 변하면 상대 Threshold도 변하므로 합격 판정은 고정 절대 nit Threshold를 우선한다.

### 8.2 변화량

낮을수록 좋은 지표의 개선율은 다음으로 통일한다.

```text
improvement_percent = 100 * (baseline - candidate) / max(abs(baseline), epsilon)
```

Baseline이 사실상 0이면 퍼센트 대신 절대 변화량을 표시한다. 단일 가중 점수로 모든 후보를 숨겨서 순위화하지 않고, 선택 영역 개선, 전체 악화, 구조 크기, 제약 위반의 Pareto Front를 기본으로 보여준다.

### 8.3 Monte Carlo 불확실성

1. Baseline과 Candidate는 같은 Seed와 동일한 Ray 생성 순서를 사용하는 Common Random Numbers 방식을 기본으로 한다.
2. 현재 Receiver Grid의 Flux 제곱합으로 각 Run의 표준 오차를 추정한다.
3. 두 Run의 공분산 또는 Ray Pair ID가 저장되기 전까지 변화량 신뢰구간은 독립 표본으로 보수적으로 계산한다.
4. 향후 `path_id` Pairing이 구현되면 Paired Difference 분산으로 교체할 수 있다.
5. 95% 신뢰구간이 0을 가로지르거나 상대 표준 오차가 Gate를 넘으면 결과는 `inconclusive`다.
6. Auto-convergence가 서로 다른 Ray 수에서 끝났다면 유효 표본 수와 종료 조건을 함께 표시한다.
7. Prediction은 검증 결과가 아니다. Draft 카드에는 “예상”, 완료된 재해석 비교에는 “검증” 라벨을 사용한다.

초기 권장 Gate는 제품 설정으로 노출하지 않고 검증을 거쳐 확정한다. 예시 기본값은 Route Flux 포착률 90% 이상, 선택 영역 상대 표준 오차 10% 이하, 최소 Hit 수 100이다.

## 9. Geometry 및 BVH 성능 전략

### 9.1 현재 문제

현재 Trace Geometry 캐시 키에는 Transform, 제외 Component, ROI, 보존 Emitter Face와 함께 `preview_blockers` 전체가 들어간다. 따라서 차광 Box의 위치나 치수만 바뀌어도 Base CAD Mesh의 Trace Geometry와 BVH를 다시 준비한다. 후보 수가 늘면 재해석보다 준비 비용이 우세해질 수 있다.

### 9.2 목표 구조

1. `PreparedTraceGeometry`를 정적 Base Geometry와 동적 Auxiliary Geometry로 분리한다.
2. Base Key에는 CAD Scene, Transform, 제외 Component, ROI, Precision Mesh 버전만 포함한다.
3. Auxiliary Key에는 Suggestion ID, Geometry 파라미터, Optical Profile Signature만 포함한다.
4. Base CAD는 기존 BVH를 재사용하고, 소수의 Blocker Triangle은 작은 별도 BVH 또는 Analytic Oriented Box Intersector로 처리한다.
5. 각 Ray는 Base와 Auxiliary의 최근접 Hit를 각각 구한 뒤 더 가까운 Hit를 선택한다.
6. 후보 치수 변경 시 Auxiliary만 재구성한다. Base Triangle 배열과 GPU 업로드 버퍼는 재사용한다.
7. Candidate가 정식 CAD Geometry로 승격되면 새 Scene Signature를 발급하고 Base Geometry를 다시 만든다.

### 9.3 실행 전략

- Phase 1은 한 번에 하나의 Candidate를 순차 실행한다.
- Phase 2는 저해상도 Coarse Pass로 많은 후보를 줄인 뒤 상위 후보만 정밀 재해석하는 Successive Halving을 사용한다.
- 동일 후보 재실행은 Request Signature로 결과를 재사용할 수 있다.
- 중지된 Run의 부분 결과를 완료 결과로 재사용하지 않는다.
- CPU/GPU, Base/Auxiliary 교차 결과가 동일해야 한다. GPU가 요청되었더라도 실제 GPU 실행 증거가 없으면 GPU 성능으로 표기하지 않는다.
- Cache Hit는 성능 정보일 뿐 물리 정확성 증거가 아니다.

## 10. 기구 설계 제약

### 10.1 Phase 1 필수 제약

- 사용자가 지정한 Host Face와 설계 허용 영역
- 최소/최대 폭, 높이, 두께
- Host Face 법선 방향 및 반전 여부
- Component/Receiver/Emitter/보호 개구 Keep-out
- 최소 간극
- 차광 재료 또는 광학 프로파일

이 정보가 없으면 Draft는 만들 수 있어도 “기구 적용 가능”으로 표시하지 않는다.

### 10.2 후속 제약

- 금형 Pull Direction과 최소 Draft Angle
- 최소 Fillet/Corner Radius
- 균일 두께 및 Sink/Warpage 위험
- 성형 공정과 재료별 최소 Feature 크기
- 체결부, 리브, Boss, Cable, Speaker, PCB, 이동 부품 간섭
- 조립 공차 및 열팽창 Clearance
- 질량, 원가, Tooling 복잡도
- 기존 리브와의 연결 또는 단절 규칙

각 제약값에는 출처를 기록한다.

- `user`: 사용자가 직접 입력
- `cad_derived`: CAD Metadata나 기하에서 계산
- `unknown`: 확인되지 않음

기구 가능성 라벨은 다음을 사용한다.

- `unverified`: 광학 형상만 존재
- `mesh_screened`: Tessellated Mesh 기준 충돌·간극 Screening 통과
- `cad_verified`: B-rep/CAD Kernel 기준 간섭·Feature 생성 검증 통과

AABB는 Broad-phase 후보 제거에만 사용한다. 실제 통과 판정은 Triangle 거리/교차를 사용하고, CAD 단계에서는 B-rep 검사로 다시 확인한다.

## 11. CAD 연계 경계

현재 Pipeline은 Tessellated Mesh 기반이며 원본 Feature Tree를 편집하거나 안정적인 B-rep Topology를 유지하는 CAD Writer 계약이 없다. 따라서 CAD 연계는 다음 경계를 지킨다.

1. Phase 1~3에서는 원본 CAD 파일을 수정하지 않는다.
2. 채택한 Suggestion은 파라미터 JSON과 Viewer Overlay로 저장한다.
3. 첫 CAD 출력은 원본과 분리된 AP242 STEP 보조 부품 또는 차광 Feature 파일로 내보낸다.
4. 내보내기에는 Suggestion ID, Host Component/Face 참조, 단위, 좌표계, 광학 프로파일, 제약 상태를 담은 Sidecar JSON을 함께 제공한다.
5. 원본 부품에 Boolean Fuse/Cut을 자동 적용하는 기능은 안정적인 B-rep 재로딩, Topology Naming, Face Remap이 구현되기 전에는 제공하지 않는다.
6. CAD 출력은 `validated`와 사용자 승인 상태를 요구한다. 단, 광학 `validated`가 금형 승인이나 간섭 검증을 의미하지 않음을 명시한다.
7. Source Face가 CAD 재저장 과정에서 바뀌면 자동 추측 연결하지 않고 `stale`로 전환한다.

## 12. 단계별 구현 계획

### Phase 0 — 신뢰 가능한 기반

- Preview/Precision 차광 광학 프로파일 단일화
- `mitigation_workspace` 저장·복원 계약과 V2 Round-trip
- `path_attribution.v1` 온라인 집계
- Stable Path/Emitter/Source Face 식별자
- Legacy 결과의 `diagnostic_sample` Fail-closed 표시
- Baseline/Candidate 비교 서명과 통계 Gate

완료 조건: P0 Acceptance Test가 모두 통과하고 기존 결과가 자동 제안에 잘못 사용되지 않는다.

### Phase 1 — 추천 전용 MVP

- Receiver 영역 선택
- Route Top-K와 Tail 포착률 표시
- 사용자 Host Face 및 허용 영역 지정
- `blocker_box_v1` Draft 1~3개 생성
- 원본 Baseline을 변경하지 않는 Candidate Case 생성
- 단일 Candidate 순차 재해석
- 검증 지표와 불확실성 비교
- 채택/거부/수정 및 프로젝트 저장

완료 조건: 원본 CAD를 수정하지 않고 선택 영역 개선을 재현 가능하게 검증한다.

### Phase 2 — 다중 후보 자동 탐색

- 위치, 폭, 높이, 두께, Offset 범위 탐색
- Coarse-to-fine 및 Successive Halving
- Base Geometry/BVH와 Auxiliary Geometry 분리
- Pareto 후보 비교
- 오류·취소·재시작 가능한 Queue
- Batch Case 기능과 결과 자동 저장 연계

완료 조건: 동일 조건에서 Baseline과 여러 Candidate를 안전하게 순차 실행하고 상위 후보를 정밀 검증한다.

### Phase 3 — 기구 제약 최적화

- Keep-out, 간극, 충돌, 최소 두께
- Draft Angle, Pull Direction, Fillet
- 리브/립 등 `blocker_box_v1` 이후 형상
- Mesh Screening과 CAD Verification 분리
- 공정/재료별 Constraint Set

완료 조건: 광학 개선 후보 중 제약 위반 후보를 Fail-closed로 제외하고 검사 근거를 표시한다.

### Phase 4 — CAD 연계

- 독립 AP242 차광 부품 또는 Feature 내보내기
- Sidecar 파라미터/Provenance
- CAD Kernel 기반 충돌 및 간극 확인
- 안정적인 Topology Naming과 Face Remap
- 사용자 승인 후 선택적 원본 조립체 연계

완료 조건: CAD 결과가 원본과 분리되어 추적 가능하고, 재불러오기 후 Suggestion과 대응한다.

## 13. UI/UX 원칙

1. 기본 화면에는 선택 영역, 근거 신뢰도, Draft 수, 검증 상태, 핵심 지표, 차단 경고만 표시한다.
2. 알고리즘 설명, 포착률 의미, 불확실성 계산, 제약 상세는 제목 옆 작은 `(?)` 도움말에 둔다.
3. 도움말은 Pointer Hover뿐 아니라 Keyboard Focus와 Click/Tap으로 열 수 있어야 한다.
4. “자동 개선”, “최적 구조” 같은 과도한 용어를 피한다. 검증 전에는 “제안”, 검증 후에는 “검증된 Candidate”를 사용한다.
5. 3D Draft Overlay에는 Host Face, 법선 방향, 치수 Handle, 금지 영역, 충돌 상태를 명확히 구분한다.
6. Baseline과 Candidate의 이름, 변경점, 실행 조건, 상태를 항상 함께 표시한다.
7. 원본 CAD 변경, Suggestion 삭제, Candidate 일괄 삭제는 확인 절차를 둔다.
8. 긴 계산은 진행 단계와 취소를 제공하고, 실패한 Candidate가 나머지 Queue를 중단할지 계속할지 정책을 명시한다.
9. 신뢰도 부족, Scene 불일치, 기구 미검증은 숨길 수 없는 경고로 유지한다.

## 14. Acceptance Test

### 14.1 P0 데이터 및 물리 일관성

1. 동일 단순 Scene과 동일 Blocker에서 Preview와 Precision의 Blocker Profile ID, 반사율, 흡수율이 일치한다.
2. Catch-all `default` 프로파일의 유무가 Blocker 광학 결과를 바꾸지 않는다.
3. 단일 Case V2 저장·복원 후 Blocker, Allowed Zone, Suggestion, Host Face, Profile, 상태, 검증 Run ID가 동일하다.
4. Scene Signature가 바뀐 프로젝트를 열면 Suggestion은 `stale`이며 자동 적용되지 않는다.
5. Legacy 결과는 `diagnostic_sample`로 표시되고 자동 추천 Gate를 통과하지 못한다.

### 14.2 `path_attribution.v1`

6. 각 Receiver Cell에서 Top Route Flux와 Tail Flux의 합이 Cell 총 Flux와 허용 오차 내 일치한다.
7. 모든 Cell 합은 Receiver Grid 총 Flux, Direct/Reflected 합, Contribution Summary와 일치한다.
8. Direct-only, 1-bounce, 2-bounce Synthetic Scene에서 Route Sequence와 Depth가 기대값과 일치한다.
9. 같은 Seed와 입력에서 Route ID와 집계 결과가 재현된다.
10. Top-K를 줄여도 총 Flux와 Tail은 보존되고 포착률만 변한다.
11. Terminal Segment Reservoir의 포함 확률과 Seed가 저장되며, 한도를 넘겨도 편향 없는 추정 규칙을 만족한다.
12. Emitter ID, Component ID, Source Face ID가 Transform과 Precision Tessellation 후에도 올바르게 연결된다.

### 14.3 Draft 및 Case 안전성

13. Host Face가 없거나 모호하면 Draft 생성이 차단된다.
14. Host Face 좌표계에서 생성한 Box의 중심, 방향, 폭, 높이, 두께가 Viewer와 Trace Geometry에서 일치한다.
15. Candidate 생성 전에는 Ray Tracing 입력과 Baseline Case가 바뀌지 않는다.
16. Candidate 적용·수정·삭제 후에도 Baseline의 Workspace와 결과 Hash가 유지된다.
17. Blocker ID는 후보 순서가 바뀌어도 결과와 Suggestion에 안정적으로 연결된다.

### 14.4 비교와 불확실성

18. 선택 Receiver 영역의 Baseline/Candidate Flux는 Grid Cell 직접 합산값과 일치한다.
19. Ray 수, Seed, Emitter, Receiver, Profile 또는 설정이 다르면 비교가 `invalid`다.
20. 95% 신뢰구간이 0을 가로지르면 수치 평균이 좋아져도 `inconclusive`다.
21. 선택 영역 개선과 보호 영역 악화가 동시에 발생하면 자동 채택되지 않는다.
22. Absolute Threshold 면적은 동일 Threshold로 비교되고 상대 Threshold 면적은 보조 지표로 표시된다.

### 14.5 성능과 장애 복구

23. Blocker 치수만 바꾼 Candidate에서 Base Geometry/BVH Cache Hit가 발생하고 Auxiliary만 갱신된다.
24. 통합 BVH와 Base/Auxiliary 분리 교차의 최근접 Hit, Face/Blocker ID, 결과 Flux가 허용 오차 내 일치한다.
25. Candidate Run은 순차 실행되고 하나의 실패가 정책에 따라 기록된 뒤 다음 Run을 안전하게 계속하거나 중지한다.
26. 앱 재시작 후 Queued/Completed/Failed 상태와 완료 결과를 복원한다.
27. 취소된 부분 결과는 `completed` 또는 `validated`로 표시되지 않는다.

### 14.6 기구 및 CAD 경계

28. Keep-out, 최소 간극, 최소 두께 위반은 Blocking Violation으로 처리된다.
29. AABB만 통과한 후보는 `mesh_screened`가 되지 않으며 Triangle 거리/교차 검사가 필요하다.
30. `unverified` 또는 `mesh_screened` 후보가 `cad_verified`로 잘못 표시되지 않는다.
31. CAD 출력은 원본을 덮어쓰지 않고 독립 파일과 Sidecar를 생성한다.
32. 재불러온 CAD에서 Face Remap이 모호하면 Suggestion은 `stale`다.

### 14.7 UX와 접근성

33. 긴 설명은 기본 화면에 노출되지 않고 관련 제목의 `HelpTooltip`에 있다.
34. 도움말과 Draft 조작은 Keyboard만으로 접근 가능하다.
35. 데이터 부족, 기구 미검증, 비교 무효 경고는 도움말 안에만 숨지 않고 화면에 보인다.
36. 삭제, 원본 CAD 연계, Candidate 일괄 제거는 사용자 확인 없이 실행되지 않는다.

## 15. 명시적 비목표

- 1차 버전에서 원본 CAD를 자동 편집하거나 덮어쓰지 않는다.
- 기존 `stored_paths`를 전체 Ray 모집단으로 간주하지 않는다.
- Preview 또는 표시 Tessellation 결과를 최종 정량 결과로 주장하지 않는다.
- 금형 성형성, 공차, 간섭을 검증하지 않은 후보를 생산 가능 형상으로 표시하지 않는다.
- 전역 최적해 또는 유일한 원인 경로를 보장하지 않는다.
- 현재 Ray Engine이 지원하지 않는 투과, 굴절, 편광, 파장별 재료 물리를 자동으로 추정하지 않는다.
- 초기 버전에서 임의 Free-form CAD 형상이나 Generative CAD를 만들지 않는다.
- 전용 Queue와 자원 격리가 준비되기 전에는 여러 CPU/GPU Candidate를 동시에 실행하지 않는다.
- 사용자의 Host Face 또는 설계 허용 영역 없이 자동으로 기구 구조를 확정하지 않는다.
- 광학 `validated` 상태를 CAD, 금형, 양산 승인으로 해석하지 않는다.
- Cache Hit 또는 GPU 요청 상태를 물리 정확성이나 GPU 실행 성공의 증거로 사용하지 않는다.

## 16. 권장 구현 순서

1. P0-1 차광 광학 계약 단일화
2. P0-2 `mitigation_workspace` 저장·복원
3. `path_attribution.v1` 정확 총량과 Bounded Route 집계
4. 비교 서명, 오차 계산, Fail-closed Gate
5. Receiver 영역에서 Host Face로 이어지는 추천 전용 UX
6. Baseline/Candidate 단일 순차 재해석
7. Base/Auxiliary Geometry 및 BVH 분리
8. 다중 후보 탐색과 Batch Case 연계
9. 기구 Constraint Set
10. 독립 CAD Feature 내보내기
