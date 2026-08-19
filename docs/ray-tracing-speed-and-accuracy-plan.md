# Ray Tracing 계산 고속화 및 정확도 향상 방안

## 목적

Receiver Heatmap 해상도를 높이고 수렴성을 확보하기 위해 Ray 수와 Resolution을 증가시키면 계산 시간이 크게 늘어난다. 본 문서는 도구의 복잡성을 과도하게 높이지 않으면서 계산 시간을 단축하고, 결과의 정밀도와 실제 현상 재현 정확도를 함께 개선하기 위한 개발 방향을 정리한다.

## 우선 적용 권장 기능

### 1. 계산 품질 프리셋

사용자가 세부 옵션을 매번 조정하지 않도록 다음 세 가지 실행 모드를 제공한다.

| 모드 | 용도 | 권장 동작 |
|---|---|---|
| Preview | 구조와 대략적인 Ray 경로의 빠른 확인 | 낮은 Ray 수, 낮은 Heatmap Resolution, 제한된 Stored Paths |
| Standard | 일반 구조 비교 및 반복 검증 | 중간 Ray 수와 Resolution, 자동 수렴 사용 |
| Final | 최종 보고서와 정밀 비교 | 높은 Ray 수와 Resolution, 엄격한 수렴 기준 |

프리셋을 선택해도 전문 사용자는 세부 설정을 직접 변경할 수 있도록 한다.

### 2. 계산 대상 Receiver 선택

여러 Receiver가 등록되어 있더라도 현재 분석에 필요한 Receiver만 선택해 계산할 수 있도록 한다. 같은 구조에서 Receiver 1, Receiver 2를 각각 검토할 때 불필요한 Heatmap 누적과 결과 집계를 줄일 수 있다.

### 3. Adaptive Heatmap

처음부터 Receiver 전체를 고해상도로 계산하지 않고 다음 단계로 처리한다.

1. 낮은 해상도로 전체 Receiver를 계산한다.
2. 빛이 도달하거나 휘도 변화가 큰 영역을 찾는다.
3. 해당 영역만 셀을 세분화해 추가 계산한다.
4. 최종 결과에서는 셀 면적을 반영해 Peak, Mean, Flux 및 광영역을 산출한다.

빛이 거의 없는 넓은 영역에 동일한 계산량을 소비하지 않으므로 국부 빛샘 분석에 특히 효과적이다.

### 4. CPU 병렬 계산

Ray 묶음을 CPU 코어별 작업으로 나눈 뒤 결과를 합산한다. 현재 적용된 BVH는 개별 Ray와 삼각형의 교차 탐색을 줄이고, CPU 병렬화는 여러 Ray를 동시에 처리한다. 두 기능은 서로 대체 관계가 아니며 함께 사용할 때 효과가 크다.

병렬화 시 다음 결과가 단일 실행과 통계적으로 동일한지 검증해야 한다.

- Receiver별 도달 Ray 수
- Total Receiver Flux
- Peak/Mean nit
- Heatmap 셀 누적값
- 반사 횟수와 Component 기여도
- 동일 Seed 조건의 재현성

## 추가 고속화 방안

### BVH 캐시 활용

CAD 형상, ROI, Transform, Trace 제외 조건이 바뀌지 않았다면 기존 BVH를 재사용한다. Material 광학값, Emitter 출력, Receiver 조건, Ray 수만 변경한 경우에는 형상 가속 구조를 다시 만들 필요가 없다.

### Stored Path와 상세 통계 분리

Stored Paths는 시각화용 대표 경로이므로 전체 계산 Ray 수와 독립적으로 제한한다. Preview에서는 적게, Final에서는 필요한 수준으로 늘린다. Component 상세 기여도와 Representative Sequences도 사용자가 요청한 경우에만 상세 저장하도록 하면 메모리와 후처리 시간을 줄일 수 있다.

### 벡터화 및 JIT 최적화

Ray 생성, 방향 계산, Receiver 좌표 변환, Heatmap 누적처럼 반복량이 큰 구간을 배열 단위로 처리한다. 프로파일링 결과 Python 반복문 비중이 높다면 NumPy 벡터화 또는 Numba JIT 적용을 검토한다.

### Importance Sampling

Receiver에 도달할 가능성이 높은 방향이나 Specular lobe에 Ray를 더 많이 배분하고, 통계 가중치를 적용해 편향 없는 결과를 만든다. 단순 균등 방출보다 Receiver 도달 Ray가 적은 구조에서 효율적이지만, 가중치 검증이 필요하므로 후순위로 적용한다.

## 정밀도와 정확도의 구분

- **정밀도 개선**: Ray 수 증가, 작은 Pixel Size, 높은 Resolution, 수렴 기준 강화처럼 Monte Carlo 노이즈를 줄이는 작업이다.
- **정확도 개선**: 실제 Material 반사율, Specular/Diffuse 분포, Gaussian 폭, Emitter 휘도와 방향, Receiver 방향·면적·Acceptance Angle, CAD 간극을 실물과 맞추는 작업이다.

Ray 수를 늘려 Error Estimate가 낮아져도 Material이나 광원 조건이 실제와 다르면 실물 빛샘을 정확히 재현하지 못한다. 따라서 정밀도와 정확도를 별도로 검증해야 한다.

## 정확도 향상 점검 항목

### Material

- 측정 파장별 Reflectance와 Absorption 적용
- Specular/Diffuse 비율 적용
- SECC 등 Gaussian Scattering 재질의 분포 폭 적용
- Reflectance + Loss 에너지 보존 검증
- 임의 기본값과 실측값 구분 표시

### Emitter

- 완제품 휘도와 선택 면적의 관계 확인
- 다중 CAD Surface의 면적 가중 방출 및 면별 Normal 사용
- 광원의 각도 분포 검증
- 선택한 CAD Face가 변경되지 않았는지 확인

### Receiver

- Front 방향과 X+/Y+ 축 확인
- Acceptance Angle 확인
- CAD Surface 실제 면적과 Datum Plane 크기 확인
- Heatmap Pixel Size와 Resolution의 상호 계산 확인
- 구조 비교 시 동일 Receiver 조건 사용

### Geometry

- CAD 단위와 Scale 확인
- 실제 간극과 부품 위치 확인
- Trace On/Off 및 Emitter-only Component 조건 확인
- 곡면 Tessellation 품질과 누락된 면 확인

## 권장 실행 절차

1. Preview 모드로 Emitter 방향, Receiver 방향, ROI, Trace 조건과 주요 반사 경로를 확인한다.
2. 선택 Receiver만 Standard 모드로 계산한다.
3. Error Estimate와 Peak-area Error를 확인한다.
4. 수렴하지 않은 경우 전체 Resolution을 즉시 높이기보다 Ray 수 또는 문제 영역의 해상도를 단계적으로 높인다.
5. 구조 후보를 비교한 뒤 최종 후보만 Final 모드로 계산한다.
6. 실제 측정값이 있다면 Peak nit뿐 아니라 위치, 광영역, Total Flux와 경로를 함께 비교한다.

## 개발 우선순위

1. Preview / Standard / Final 프리셋
2. 계산 대상 Receiver 선택
3. Adaptive Heatmap 및 영역별 수렴
4. 프로파일링 후 CPU 병렬화와 벡터화
5. Importance Sampling

초기 구현은 1~4번에 집중하는 것이 기능 복잡도 대비 체감 성능 개선 효과가 가장 크다.

## PERF-3B 구현 상태

- Batch 교차 입출력 계약과 CPU reference adapter를 완료했다.
- Virtual-plane `max_depth <= 1` 실행 경로는 명시적 runtime `batch` 요청에서
  primary/secondary wavefront batch를 사용한다.
- 같은 seed에서 Receiver grid, flux, contribution, reflection summary와
  stored path가 scalar 실행과 exact 일치한다.
- PERF-3B-2A에서 명시적 runtime `batch`, fast virtual-plane emitter와
  `max_depth >= 2` 조합을 compact depth wavefront로 확장했다. Face/polygon
  emitter는 계속 legacy scalar를 사용한다.
- 기본 `auto`는 depth와 관계없이 scalar/Python CPU를 유지하며 Numba를
  import하거나 probe하지 않는다. 따라서 GPU·Numba가 없는 PC의 기존 실행과
  Receiver/contribution/path 정량 결과는 바뀌지 않는다.
- Specular/threshold처럼 reflection random draw가 없는 multi-bounce는 legacy
  scalar와 Receiver grid, flux, contribution, reflection summary, stored path가
  exact 일치한다.
- Lambertian, Gaussian, mixed 또는 실제 Russian-roulette draw가 있는
  wavefront는 `per_primary_seeded_v1`을 사용한다. 같은 wavefront seed에서는
  chunk 크기, 반복 실행과 Python/native provider가 달라도 exact하지만 legacy
  depth-first scalar와 개별 ray/grid가 exact 같다는 계약은 아니다.
- Stochastic 결과를 비교할 때는 후보 간 dispatch를 섞지 말고 같은 seed와
  dispatch를 사용한다. Legacy scalar와 wavefront의 타당성 비교는 여러 seed의
  Receiver flux, hit ratio, error estimate와 에너지 보존으로 판단한다.
- 현재 reference batch는 native 가속이 아니어서 100,000-ray synthetic의
  최선 4,096 chunk 기준 scalar 대비 처리량이 약 28.9% 낮다.
- PERF-3B-2에서 optional strict-float64 Numba BVH provider를 연결했다. 실제
  50,944-triangle CAD 교차 micro는 독립 실행에서 약 `48.98~50.45x`,
  face/distance mismatch `0`을 기록했다.
- 같은 provider를 사용한 100,000-ray 단일 반사 synthetic end-to-end는
  독립 실행에서 `0.961~1.009x`의 baseline 수준으로 자동 선택 기준을 넘지
  못했다. 따라서 기본 `auto`는 Numba를 probe하지 않고 기존 Python scalar
  경로를 유지한다.
- PERF-3B-1 parent와 기본 scalar를 교대 13회 측정한 runtime 차이는
  `+0.42%`로 3% 회귀 gate 안의 측정 잡음 수준이며 결과는 exact 일치했다.
- 실제 45,167-triangle, 100,000-ray, depth 10, stored-path 500 workload의
  canonical 1,024 wavefront 중앙값은 `7.0649초`, p95 `7.3970초`다. Python
  scalar `26.1930초` 대비 `3.71x`, native scalar 대비 `1.59~1.62x`다.
- Stored-path quota 밖 경로의 객체 생성을 생략해 같은 4,096 workload를
  `8.8017초`에서 `7.4763초`로 줄였다. `931`개를 materialize하고 `99,069`개를
  생략했으며 정량 결과와 path quota는 보존했다.
- 세 seed stochastic 비교에서 legacy 대비 hit/flux 평균 차이는 약 `-0.9%`였고
  95% CI가 0을 포함했다. Bias 증거는 없지만 표본이 작아 기본 `auto`는 더 큰
  통계 검증 전까지 scalar로 유지한다.
- PERF-3B-2B는 surface point/normal을 row-aligned NumPy batch로 복원하고
  Receiver 우선 stored-path quota를 ordered dead-end queue로 O(1) 판정한다.
  Point/normal, quantitative 결과와 path 교체 순서는 기존 scalar/2A와 exact다.
- Runtime-only `wavefront_planner="numba_cpu"`는 strict-float64
  `deterministic_reflection_v1`로 threshold의 `none`/`specular` row만 처리한다.
  Mixed/Lambertian/Gaussian/Russian-roulette row는 Python sidecar를 사용한다.
- Planner hard failure phase는 `input_prepare`, `initialize`, `execute`,
  `result_validation`이다. 같은 depth의 deterministic candidate 전체를 Python으로
  replay하고 circuit breaker를 연다. Fallback row count에는 stochastic sidecar나
  native retry를 중복 집계하지 않는다.
- 기본 `auto`와 scalar는 Numba runtime을 import하거나 native provider를
  probe하지 않는다. 따라서 GPU·Numba가 없는 PC의 기존 CPU 실행은 이번
  단계에서도 바뀌지 않는다.
- 실제 `50,944 -> 45,167` triangle ROI, 100,000 ray, depth 10, seed 42,
  summary, paths 500, runtime 기본 chunk 1,024 canonical은 중앙값
  `5.2553초`다. 같은 1,024 조건 2A parent `7.0649초` 대비 `1.344x`, Python
  scalar `26.193초` 대비 `4.984x`, 역사적 Numba scalar 약 `11.42초` 대비 약
  `2.173x`다.
- 위 실제 ROI 결과는 Receiver `12,652`, surface `225,482`, terminated `87,348`,
  flux `0.040176617410112817`, query row `309,119`, path `500`이다. 현재
  wavefront 반복과 `auto`/`python_cpu` planner 사이 ordered payload는 exact다.
  Legacy/Numba scalar는 mixed stochastic 계약상 timing reference다.
  Materialized/skipped path는 `931/99,069`다.
- Canonical planner가 `auto`라 native reflection planner attempt는 `0`이었다.
  실제 모델도 전부 mixed라 explicit native에서 Python sidecar 대상이다. 따라서
  이번 실제 speedup은 surface geometry batch와 O(1) quota의 효과이며 compiled
  planner speedup으로 해석하지 않는다.
- Deterministic 10,000-ray depth-10 synthetic 네 scenario에서 Numba planner는
  Python planner 대비 `1.078~1.241x`, semantic mismatch `0`이었다. 실제 mixed
  ROI와 배포 gate가 남아 기본 `auto` 승격 근거로 사용하지 않는다.
- Stable-source compact paths-on 교대 재측정은 1,024 `5.1601초`, 4,096
  `5.1863초`로 약 `0.51%` 차이의 동률 범위였다. 별도 synthetic depth-10
  `tracemalloc`의 Python allocation peak는 약
  `9.65 -> 37.64 MiB`, Stop 원자 단위는 4배다. 이 값은 실제 ROI process
  RSS가 아니다. 현재 기본은 메모리/Stop 응답성이 유리한 1,024다.
- PERF-3B-2B도 아직 백만 ray 목표를 달성하지 않았다. `5.2553초`의 선형
  환산은 약 `52.6초`다. 다음은 SoA ray state, compact event tape, compiled
  ordered reducer, `counter_rng_v2`, CUDA 순서다.
- GPU backend는 capability/precision/fallback 계약까지 준비한 뒤 추가한다.
