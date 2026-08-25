# Ray Tracing 성능 가속 계획

## 목적
- 계산 정확도와 데이터 계약을 유지하면서 반복 설계에 필요한 실행 시간을 줄인다.
- GPU가 없는 PC에서도 프로그램 전체 기능을 사용할 수 있도록 CPU 경로를 항상 유지한다.
- 특정 가속 라이브러리에 종속되지 않도록 계산 백엔드를 단계적으로 교체한다.

## 백엔드 계층

### 1. CPU 기준 경로
- 이름: `reference_cpu`
- 역할: 결과 정합성 검증, 개발 디버깅, 가속 라이브러리가 없는 PC의 안전한 대체 경로
- 특징: 순수 Python 기반으로 가장 이식성이 높지만 대형 CAD와 많은 ray에서 느리다.

### 2. 최적화 CPU 경로
- 이름: `python_numpy_cpu`
- 현재 기본 경로
- 적용 내용:
  - 가상 평면 광원의 NumPy batch sampling
  - 저장 대상 ray path에만 `RayHit` 객체 생성
  - receiver 좌표계와 판정 상수 사전 계산
  - face별 optical property 사전 캐시
  - 반사·산란 벡터 계산의 Python 호출과 중복 정규화 감소
  - batch surface point/normal materialization
  - Receiver 우선 stored-path quota의 O(1) 판정

### 3. CAD 교차 가속 경로
- 예정 이름: `accelerated_cpu`
- 현재 prototype: runtime-only `numba_cpu` opt-in
- 적용 후보:
  - 자체 BVH
  - Intel Embree
  - Open3D `RaycastingScene`
- 목적: 실제 STEP/X_T에서 생성된 수십만~수백만 triangle에 대한 ray-scene intersection 병목 제거
- 원칙: 교차점의 `face_index`, 거리, 위치, normal이 현재 데이터 계약과 동일해야 한다.

### 4. GPU 경로
- 현재 이름: `gpu_cuda`
- 현재 구현: Numba CUDA strict-float64 custom BVH kernel
- 후속 후보: NVIDIA OptiX 또는 더 넓은 device-resident pipeline
- 목적: 대량 ray와 다중 반사 계산의 처리량 확대
- 조건: 지원 GPU, 드라이버, CUDA runtime 또는 배포 가능한 GPU 실행 환경 필요

## GPU가 없는 PC의 동작
- 프로그램을 사용할 수 있다.
- CAD import, 3D viewer, ROI, Transform, Material, Emitter, Receiver 기능은 GPU ray tracing 지원 여부와 무관하게 동작한다.
- ray tracing은 자동으로 CPU 백엔드를 선택한다.
- 차이는 주로 ray tracing 실행 시간이다.
- 동일한 설정에서 CPU와 GPU 결과는 허용 오차 범위 내에서 동일한 통계 경향과 에너지 합계를 유지해야 한다.
- GPU 전용 기능 때문에 프로젝트 파일을 열 수 없거나 결과를 확인할 수 없는 구조는 허용하지 않는다.

## 자동 선택 정책
1. 사용자가 특정 백엔드를 강제로 지정한 경우 해당 백엔드의 사용 가능 여부를 확인한다.
2. GPU 실행 환경이 정상이라면 `gpu_cuda`를 선택한다.
3. CPU 교차 가속 라이브러리가 있으면 `accelerated_cpu`를 선택한다.
4. 그 외에는 `python_numpy_cpu`를 선택한다.
5. 실행 실패 시 한 단계 낮은 백엔드로 안전하게 대체하고 결과에 실제 사용 백엔드를 기록한다.

위 항목은 최종 목표 정책이다. PERF-3C 시점에도 project 기본
`compute_backend="cpu"`는 기존 Python CPU를 유지하며 optional provider를
probe하지 않는다. 사용자가 project를 `gpu_cuda`로 선택한 경우에만 GPU stack의
`auto`가 CUDA/Numba와 hybrid policy를 선택한다. 모든 PC의 global 자동 GPU
승격은 cold start, VRAM/Stop, clean-PC package와 다양한 driver/toolkit gate를
통과한 뒤 별도로 판단한다.

## 단계

### PERF-1: Python hot path 최적화
- 상태: 완료
- 범위:
  - 객체 생성 최소화
  - NumPy 광원 batch sampling
  - optical property 캐시
  - 반사·receiver 수치 계산 단순화
  - 반복 가능한 100만 ray benchmark

### PERF-2: CAD intersection 가속
- 상태: 1차 완료
- 우선순위:
  1. brute-force reference와 flat BVH 결과 정합성 테스트 완료
  2. 사전 계산 triangle + flat BVH CPU backend 연결 완료
  3. TV 샘플과 9,486 triangle STEP 성능 비교 완료
  4. 실제 회사 TV ROI 도면의 end-to-end 측정 필요
  5. 필요 시 Embree/Open3D adapter를 후속 비교

### PERF-3: batch 병렬화와 GPU

#### PERF-3A: 단일 반사 fast path
- 상태: 완료
- 가상 평면 emitter의 NumPy sampling과 depth 0~1 전용 경로를 적용했다.

#### PERF-3B-0: 기준 측정과 batch 계약
- 상태: 완료 (2026-08-18)
- 최신 main `86eaa4b`에서 scalar BVH micro/end-to-end 기준을 측정했다.
- `RayBatch`, `RayHitBatch`, `TriangleMesh.intersect_rays()` 계약을 추가했다.
- 최초 구현은 기존 scalar 교차를 row별 호출하는 CPU reference adapter다.
- `RayTraceConfig`와 실제 ray tracing 실행 경로는 아직 변경하지 않았다.
- 이 단계 자체는 속도 향상이 아니라 이후 backend의 정합성 기준이다.

#### PERF-3B-1: wavefront batch 연결
- 상태: 완료 (2026-08-18)
- NumPy primary ray가 이미 준비되는 virtual-plane fast path를 연결했다.
- receiver 거리를 ray별 `max_t`로 전달하고 primary/secondary ray를 각각 batch query한다.
- 결과 누적과 stored path는 원 primary row 순서로 commit해 scalar 결과를 exact 보존한다.
- 기존 65,536 sampling batch와 당시 기본 4,096 intersection chunk를 분리했다.
- Stop/progress는 시작한 intersection chunk를 원자적으로 완료한 뒤 경계에서 처리한다.
- face/polygon emitter와 `max_depth >= 2`는 기존 scalar 경로를 유지한다.
- runtime dispatch/chunk 인자는 프로젝트 파일에 저장하지 않는다.
- PERF-3B-1 완료 시점의 교차 구현은 Python row-loop CPU reference이며
  `native_batch=false`였다.
- 따라서 기본 `auto`는 scalar를 유지하고 reference batch는
  테스트/benchmark에서만 명시적으로 요청했다.

#### PERF-3B-2: native CPU prototype
- 상태: prototype 완료 (2026-08-18), 기본 자동 선택은 보류
- strict-float64 Numba BVH provider를 runtime-only opt-in으로 연결했다.
- provider import/JIT, immutable scene pack, capability와 whole-query fallback을
  기존 Python CPU 경로와 분리했다.
- 실제 50,944-triangle CAD intersection micro는 독립 실행에서 약
  `48.98~50.45x`였지만, 100,000-ray synthetic end-to-end는
  `0.961~1.009x`의 baseline 수준이어서 자동 선택 gate `1.20x`를 통과하지
  못했다.
- 기본 `auto`는 Numba를 probe/import하지 않고 기존 scalar 경로를 유지한다.
- 기존 PERF-3B-1과 같은 scalar workload를 교대 13회 측정한 결과 runtime
  중앙값 차이는 `+0.42%`로 3% 회귀 gate 안의 측정 잡음 수준이었다.
- 측정된 Numba/llvmlite module directory 약 149.8 MiB와 cold JIT 비용 때문에
  lightweight package에는 아직 포함하지 않는다.

#### PERF-3B-2A: multi-bounce wavefront와 후처리 batch
- 상태: 구현 및 canonical 반복 benchmark 완료 (2026-08-19), 명시적 opt-in
- 명시적 `batch`와 fast virtual-plane emitter의 `max_depth >= 2` 실행을
  depth별 compact wavefront로 전환했다.
- Receiver 후보는 NumPy batch로 계산하고 active origin/direction/energy/이전
  face만 다음 depth로 compact한다.
- 결과는 원 primary 순서로 commit해 contribution 누적과 stored-path quota
  정책을 보존한다. 저장될 수 없는 시각화 path의 `RayHit` materialization도
  생략한다.
- random draw가 없는 specular 경로는 legacy scalar와 exact하다. Stochastic
  scatter/Russian roulette는 `per_primary_seeded_v1`로 chunk/provider/repeat
  exact를 보장하며 legacy scalar와는 statistical parity로 비교한다.
- 기본 `auto`, face/polygon emitter는 legacy scalar/Python CPU를 유지하고
  Numba를 probe하지 않는다.
- 실제 45,167-triangle, depth 10, stored-path 500 workload에서 권장 1,024
  chunk는 중앙값 `7.0649초`, p95 `7.3970초`로 Python scalar 대비 `3.71x`,
  native scalar 대비 `1.59~1.62x`다. 4,096보다 처리량이 `3.48%` 높아 명시적
  batch runtime 기본 chunk를 1,024로 조정했다. PERF-3B-2B stable-source 실제
  ROI 교대 재측정에서도 1,024/4,096 p50 차이는 약 `0.51%`로 동률 범위였고
  1,024가 근소하게 빨라 현재 기본을 1,024로 유지한다.
- 세 seed stochastic 비교의 hit/flux 평균 차이는 약 `-0.9%`였고 95% CI가
  0을 포함했지만 표본이 작다. Bias 부재 확정과 자동 선택 승격은 더 많은 seed
  또는 1M 통계 gate 뒤로 보류한다.
- 교차 외 plan/ordered commit이 다음 병목이며, PERF-3B-2A만으로 백만 ray
  목표를 달성했다고 판단하지 않는다.

#### PERF-3B-2B: surface/path compaction과 compiled reflection planner

- 상태: 구현 및 canonical benchmark 완료 (2026-08-19), 기본 자동 선택은 보류
- `RayHitBatch`가 point/normal을 row-aligned NumPy batch로 materialize해 surface
  hit마다 scalar `HitRecord`와 tuple을 만들지 않게 했다.
- Stored-path quota는 오래된 dead-end index queue로 기존 Receiver 우선 교체
  순서를 O(1)에 보존한다.
- Runtime-only `wavefront_planner`에 `auto`, `python_cpu`, `numba_cpu`를 추가했다.
  기본 `auto`는 Python planner이며 Numba를 import/probe하지 않는다.
- Native planner는 strict-float64 `deterministic_reflection_v1` 계약으로
  `threshold`의 `none`/`specular` row만 처리한다. Stochastic/Russian-roulette
  row는 기존 `per_primary_seeded_v1` Python sidecar를 사용한다.
- `input_prepare`, `initialize`, `execute`, `result_validation` hard failure는 같은
  depth의 deterministic candidate 전체를 Python으로 replay하고 circuit breaker를
  연다. Fallback row count는 stochastic sidecar가 아니라 실패한 native candidate
  수다.
- 실제 mixed ROI final canonical `5.2553초`는 planner `auto`라 native attempt가
  `0`이다. 복원
  profile도 전부 mixed라 explicit native에서도 Python sidecar 대상이다.
  `5.2553초` 개선은 compiled planner에 귀속하지 않으며, 개선 원인은 surface
  geometry batch와 O(1) path quota다.
- Deterministic 10,000-ray synthetic 네 scenario에서는 Python planner 대비
  Numba planner가 `1.078~1.241x`, semantic mismatch `0`을 기록했다.
- Surface/path/planner fallback을 포함한 전체 Python suite `160`개가 통과했다.
- 실제 ROI의 1,000,000-ray 선형 환산은 약 `52.6초`라 최종 목표 달성을
  주장하지 않는다. SoA state와 event tape는 PERF-3B-2C에서 이어서 구현하며
  compiled ordered reducer는 이어진 PERF-3B-2C-2에서 완료했다.

#### PERF-3B-2C/2C-1: SoA state와 ordered event tape v2

- 상태: experimental 구현 및 exact regression 완료 (2026-08-19), 자동 선택 보류
- Runtime-only `wavefront_pipeline`에 `auto`, `object_reference`,
  `soa_event_tape`를 추가했다.
- `stable_active_soa_v1`은 active ray의 primary slot/index, origin/direction,
  power, source face, ray kind와 reflection seed를 owned 배열로 유지하고 stable
  row 순서로 compact한다.
- `ordered_primary_event_tape_v3`는 depth-major 계산 결과를 실제 surface event
  비례 primary-major CSR로 seal한다. Core 정량 column과 optional path geometry를
  분리해 paths-off와 quota 0은 `omitted_v1`, path 저장이 필요하면
  `full_path_v1`을 사용한다.
- Public `seal()`은 vectorized `strict_v1` validation을 유지한다. Private
  `_seal_trusted()`의 `trusted_structural_v1`은 future compiled producer/benchmark
  전용이며 일반 runtime은 선택하지 않는다. 정상 strict/trusted 결과는 byte
  exact다.
- Sealed 배열은 owned/C-contiguous/read-only다. Validation/copy/payload/peak scope를
  별도 metric으로 기록하며 byte 계측을 process RSS나 GPU memory로 해석하지 않는다.
- `python_ordered_v1` reducer는 primary 순서로 grid, flux, summary/detailed
  contribution, reflection과 stored-path quota를 replay한다. 저장 가능한 path만
  materialize한다.
- Deterministic depth 2/10과 stochastic mixed/Gaussian/Russian-roulette에서
  object-reference 대비 float bit와 dict key 순서, chunk/provider/repeat 정합성을
  exact 검증했다.
- 기본 `auto`는 `object_reference`다. v2 actual ROI p50은 object-reference
  `5.232795초`, SoA `5.121246초`로 `1.021782x`, wall `2.132%` 개선됐지만 자동
  승격 gate `>= 1.05x`에는 못 미쳤다.
- 두 경로의 semantic/grid/contribution/path hash는 여섯 measured run에서
  exact했다. 100k paths-on tape peak는 primary chunk당 최대 `680,048 bytes`였다.
  별도 10k A/B의 paths-off/on peak는 `271,080 / 643,800 bytes`, copy 회계는
  `1,131,996 / 2,952,580 bytes`였다. 이는 process RSS가 아니다.
- Strict/trusted/payload/fallback/no-probe 회귀를 포함한 전체 Python suite는
  `184 passed, 154 subtests passed`다. Unit test에는 wall-time threshold를 두지
  않는다.
- Tape를 직접 소비하는 compiled ordered reducer는 이어진 2C-2에서 완료했다.
  그 뒤 `counter_rng_v2`와 같은 SoA/tape 기반 CUDA backend를 진행한다.

#### PERF-3B-2C-2: compiled ordered summary reducer

- 상태: optional CPU native 구현, exact regression과 actual canonical 완료
  (2026-08-19), 기본 자동 선택은 Python 유지
- Runtime-only `wavefront_reducer`는 `auto`, `python_cpu`, `numba_cpu`다.
  `auto`는 Python/no-probe이고 explicit native는 SoA summary에서만 지원한다.
  Detailed contribution은 정상 Python 선택이며 native attempt/fallback이 없다.
- `ordered_summary_reducer_v1`은 primary-major tape를 serial strict `float64`,
  `fastmath=False`로 처리해 Python의 primary/event 덧셈 순서를 보존한다.
- Native output은 owned/read-only/no-alias이며 provider/consumer validation과 digest,
  staged dict/grid/path 복원을 모두 통과한 뒤 한 번만 publish한다.
- Unavailable/initialize/execute/result-validation/apply 실패는 같은 tape 전체를
  Python으로 한 번 replay하고 run-local circuit breaker를 연다. Logical count는
  native attempt와 replay를 중복 집계하지 않는다.
- Deterministic/stochastic/depth/chunk/multi-emitter, terminal-only, quota 교체,
  Stop, fallback과 concurrent run에서 object/SoA Python/native ordered JSON,
  모든 float bit와 dict insertion order가 exact다.
- Actual warm p50은 Python `5.094436초`, native `4.643004초`로 `1.097228x`,
  wall `8.861%` 개선됐다. Replay는 `2.3968x`, commit은 `1.7126x`다.
- Cold JIT `2.382357초`, optional Numba/llvmlite 배포와 단발 실행 손익 때문에
  기본 `auto`는 Python/no-probe를 유지한다. Native는 명시적 opt-in이다.
- 다음은 prepare/result-validation/apply overhead 축소, `counter_rng_v2`, CUDA
  backend 순서다.

#### PERF-3B-3: CUDA GPU backend
- 상태: strict-float64 CUDA intersection과 GPU project stack 1차 구현 완료
  (2026-08-20)
- `compute_backend="gpu_cuda"`는 batch 65,536, CUDA BVH, SoA,
  `counter_rng_v2`, Numba planner/reducer를 선택한다. `<8,192` active row는
  Numba CPU hybrid로 처리한다.
- Prepared host/device scene과 thread-local workspace를 재사용하고 capability,
  upload/kernel/download, device와 hybrid/GPU별 logical count를 기록한다.
- Face/count/grid/summary는 exact, distance/path는 abs/rel `1e-12`다.
- GPU Face emitter는 vectorized primary batch와 row별 source-face ID를 만들고
  CUDA BVH `ignore_faces`에 연결한다. 최초 Face wave는 작은 batch도 CUDA를
  직접 호출하며 이후 작은 reflection wave는 기존 CPU hybrid 정책을 따른다.
- GPU 부재는 정상 CPU 선택이다. Initialize/execute/result-validation hard failure는
  logical batch 전체 CPU replay 한 번과 run-local circuit breaker로 처리한다.
- CPU project가 기본이며 CUDA/Numba import/probe가 없다. GPU acceleration
  dependency와 NVIDIA driver/toolkit은 optional 배포 범위다.
- 실제 ROI 1M source-freeze isolated warm 3-run p50/p95는
  `7.277951 / 8.004967초`, `137,401 primary ray/s`였다.
  이는 LightTools 비교가 아니며 VRAM/Stop/clean-PC 배포 gate는 후속 검증한다.

#### PERF-3D: host overhead 제거와 run-retained ordered accumulator

- GPU project의 `wavefront_reducer_commit="auto"`는
  `run_accumulator`를 선택해 tape별 Python accumulator build/hydrate를 마지막
  1회로 줄인다. CPU project의 `auto`는 기존 `per_tape`를 유지한다.
- Reflection seed는 `numpy_splitmix64_batch_v1`, Receiver 후보는
  `numpy_numeric_batch_v2` numeric batch를 사용한다.
- Stored-path quota가 receiver-only로 포화된 뒤에는 path payload를 단조롭게
  생략한다. Dead-end replacement 가능성이 남아 있으면 full payload를 유지한다.
- Retained result는 run-local이고 성공한 native result만 저장한다. 실패 시 이전
  성공 state를 flush한 뒤 failing tape 전체를 Python으로 한 번 replay하며 Stop과
  concurrent run의 원자성은 기존 계약을 유지한다.
- 이번 단계의 retained/resident는 CPU numeric reducer accumulator만 뜻한다.
  전체 ray state GPU residency나 fused CUDA depth kernel은 후속이다.

#### PERF-4: 1억 Ray·10회 반사 목표

- 2026-08-25 RTX 3070의 1M primary·depth 10 all-survive warm 측정은
  `13.14~14.48초`, 약 `69k~76k primary ray/s`였다.
- 현재 구조의 1억 Ray 선형 환산은 약 `21.9~24.1분`이다. 10분 이하는
  device-resident wavefront, GPU Receiver/Heatmap accumulator와 fused
  traversal/shading이 필요하다.
- 5% Error 달성에 필요한 Ray 자체를 줄이기 위해 Receiver-directed importance
  sampling/Next Event Estimation과 Auto convergence sample 재사용을 병행한다.
- 상세 타당성 및 단계별 Gate는
  `docs/reports/2026-08-25_gpu-100m-ray-feasibility.md`를 따른다.

##### PERF-4A: 고정 성능 계약

- 상태: 완료 (2026-08-25)
- Face direct, stochastic two-bounce, trapped corridor depth 10을 scene hash가 있는
  고정 workload로 등록했다.
- 기준선은 `host_roundtrip`으로 고정하고 cold/warm, logical intersection,
  Receiver hit, CUDA 증거, 1억 Ray 선형 환산을 기록한다.
- 상세 계약은 `docs/perf4a-benchmark-contract.md`를 따른다.

##### PERF-4B: GPU 상주형 Wavefront

- 상태: 1차 완료 (2026-08-25)
- primary Ray 상태, Receiver 판정, CUDA BVH, optical lookup, 반사 방향, 감쇄와
  종료 판정을 한 CUDA kernel 안에서 처리한다.
- Provider 계약은 `strict_float64_resident_wavefront_v1`이며 실패한 chunk는 기존
  host-roundtrip으로 정확히 한 번 replay한다.
- RTX 3070 100k warm p50에서 stochastic depth 2는 `1.41x`, all-survive depth 10은
  `1.57x` 개선했다. Depth-10 1억 Ray 선형 환산은 `21.6분 -> 13.8분`이다.
- 공개 결과는 GPU host-roundtrip과 exact했고 fallback은 0회였다.
- CPU/CUDA 확률 수학함수 차이는 이산 exact + abs/rel `1e-12` + ULP `8` 계약으로
  검증한다. 실제 8,192-Ray 회귀 최대 ULP는 2였다.
- event tape 다운로드와 CPU ordered reducer는 남아 있으며 PERF-4C 대상이다.
- 상세 계약과 측정은 `docs/perf4b-device-resident-wavefront.md`,
  `docs/reports/2026-08-25_perf4a-perf4b-benchmark.md`를 따른다.

##### PERF-4C: GPU Receiver/Heatmap·결과 누적기

- 상태: 1차 완료 (2026-08-25)
- Provider 계약은 `strict_float64_gpu_summary_accumulator_v1`이다.
- PERF-4B event 배열을 device에 유지한 채 optical/reflection/contribution,
  Receiver flux와 heatmap을 CUDA에서 직접 누적한다.
- 일반 summary 실행은 전체 event tape 대신 compact summary와 path quota가 선택한
  경로만 CPU로 전송한다. 진단용 `gpu_accumulator="host"`는 4B 기준선을 유지한다.
- RTX 3070 100k warm p50에서 stochastic depth 2는 `1.858x`, trapped depth 10은
  `7.715x` 개선했고 전송량은 `99.925~99.978%` 감소했다.
- 이산 결과는 exact이며 GPU atomic 합산에 따른 최대 absolute error
  `5.239e-10`은 strict `1e-9` 계약을 통과했다. fallback은 0회였다.
- 100k p50 단순 선형 환산은 1억 Ray에서 약 `60.9초`와 `102.0초`지만, 실제 TV
  CAD·열·VRAM을 포함한 실측값이 아니므로 목표 달성 증거로 사용하지 않는다.
- 상세 계약과 측정은 `docs/perf4c-gpu-accumulator.md`,
  `docs/reports/2026-08-25_perf4c-gpu-accumulator.md`를 따른다.

##### PERF-4D: Compact GPU workspace

- 상태: 1차 완료 (2026-08-25)
- summary 실행의 전체 event geometry workspace를 compact scalar workspace와
  선택 path sparse retrace로 교체했다.
- RTX 3070 100k에서 workspace는 depth 2 `46.11%`, depth 10 `56.22%` 감소했다.
- wall time은 `0.970x`, `0.998x`로 동등하거나 소폭 느렸으므로 속도 개선으로
  표현하지 않는다.
- 이산 exact, strict float64 통과, fallback 0을 확인했다.
- 실제 TV ROI 장시간 VRAM·열 검증은 남아 있다.
- 상세 계약은 `docs/perf4d-compact-workspace.md`를 따른다.

##### PERF-4E: 필요한 Ray 수 감소

- 상태: primary MIS·Lambertian bounce MIS·표본 재사용 완료 (2026-08-25)
- PERF-4E-A: Lambertian/isotropic CAD face·datum Emitter의 Receiver-directed
  primary MIS를 구현했다. Gaussian/scalar-only는 source sampling으로 fail-safe
  fallback한다.
- PERF-4E-C: Auto convergence를 독립 구간 누적으로 바꿨다. `1→2→4→8배`는
  기존 15배 재실행 대신 8배 Ray만 처리한다.
- RTX 3070 직접 가시 synthetic 장면에서 seed 간 Flux 분산은 약 `7,460x`
  감소했고 CPU/GPU strict 정합성을 통과했다.
- PERF-4E-B: 순수 Lambertian 반사점에서 원래 cosine 분포와 Receiver 면적
  proposal을 혼합하는 단일 continuation-ray MIS를 구현했다. Receiver 방향 Ray도
  기존 BVH를 통과하므로 중간 차폐물은 그대로 판정된다.
- 반사광 synthetic 장면에서 20,000 Ray×12 seed 기준 Flux 분산은 약 `3,256x`
  감소했고, 작은각 근사 기준 bias는 `-0.092%`였다. CPU/GPU 이산 결과 exact와
  strict float64 허용오차를 통과했다.
- Specular는 기존 delta 경로를 유지하고 Gaussian·Mixed는 정확한 PDF 계약이
  없으므로 source sampling으로 fail-closed fallback한다.
- 실제 TV ROI 여러 seed 검증 전까지 primary sampling 기본값은 `source`다.
- 상세 계약은 `docs/perf4e-receiver-importance-sampling.md`를 따른다.

## 정합성 기준
- Random draw가 없는 같은-seed wavefront는 legacy scalar와 exact해야 한다.
- Stochastic wavefront는 같은 seed에서 chunk 크기, 반복 실행과 provider가
  달라도 exact해야 한다.
- Stochastic wavefront와 legacy scalar는 Monte Carlo stream이 다르므로
  receiver flux, hit ratio와 error estimate를 여러 seed의 통계 허용 오차로
  비교한다.
- 에너지 증가가 발생해서는 안 된다.
- face/component/material id 연결이 가속 전후 동일해야 한다.
- 성능 개선 때문에 optical assignment 우선순위가 달라져서는 안 된다.

## 현재 측정
- 장면: RT-2C 단일 평면 반사 synthetic scene
- Python: 3.13.3
- Gaussian 100,000 ray:
  - 초기: `5.126초`
  - PERF-1: `2.262초`
  - 개선: 약 `2.27배`
- Gaussian 1,000,000 ray:
  - PERF-1: `22.980초`
  - 처리량: 약 `43,515 ray/s`
- 실제 CAD에서는 triangle 수에 따라 교차 계산 비중이 크게 증가하므로 PERF-2 효과가 더 중요하다.

## PERF-2 측정
- TV 샘플 116 triangle:
  - 기존 recursive BVH: 약 `21,767 ray/s`
  - flat BVH: 약 `38,983 ray/s`
  - 개선: 약 `1.79배`
- Helical Gear 9,486 triangle:
  - brute-force: 약 `219 ray/s`
  - 기존 recursive BVH: 약 `4,972 ray/s`
  - flat BVH: 약 `19,099 ray/s`
  - 기존 BVH 대비 약 `3.84배`
- reference mismatch: `0`

위 PERF-2 TV 수치는 과거 116 triangle tessellation 기준이다. 최신 adaptive
tessellation에서는 full STEP이 106,352 triangle이므로 직접적인 전후 비교
기준으로 사용하지 않는다.

## PERF-3B 진입 기준 측정 (2026-08-18)

측정 환경:
- CPU: Intel Core i7-10700, 8 core / 16 thread
- Python: 3.13.3
- 기준 commit: `86eaa4b`
- seed: `20260717`

교차 micro baseline:
- CAD: `tv_leakage_roi_right_bottom_no_gap.stp`
- triangle: 50,944
- warm scalar flat BVH, 50,000 ray, 5회 중앙값
- 실행 시간: `2.3079초`
- 처리량: `21,664.6 ray/s`
- cold import: `0.9828초`
- BVH build: `0.9147초`
- brute-force reference mismatch: `0`

실제 저장 프로젝트 smoke baseline:
- 활성 ROI triangle: 45,167 / 50,944
- datum-plane Lambertian emitter, depth 10, 10,000 ray, 3회 중앙값
- 실행 시간: `2.5735초`
- 처리량: `3,885.8 primary ray/s`
- receiver hit: 1,276
- surface hit: 22,291
- 세 실행의 결과가 동일했다.

프로파일링에서는 Python BVH traversal, 특히 ray-AABB 판정이 교차 시간의
대부분을 차지했다. 따라서 batch 경계를 고정한 뒤 native/GPU traversal로
교체하는 개발 순서가 타당하다.

계약 구현 후 동일 조건 재현 benchmark:
- scalar BVH: `22,864.8 ray/s`
- CPU reference batch, size 256: `20,079.1 ray/s` (`0.878x`)
- CPU reference batch, size 4,096: `20,451.3 ray/s` (`0.894x`)
- CPU reference batch, size 50,000: `20,271.0 ray/s` (`0.887x`)
- scalar/batch face mismatch: `0`
- scalar/batch distance mismatch: `0`
- brute-force/BVH mismatch 50-ray sample: `0`

현재 adapter는 Python row loop와 배열 변환/결과 할당 비용 때문에 scalar보다
약 10~12% 느리다. 이는 예상된 reference 비용이며 성능 개선으로 계산하지
않는다. 이후 native/GPU 구현은 같은 계약과 mismatch `0`을 유지하면서 이
기준을 넘어야 한다.

## PERF-3B-1 wavefront 측정 (2026-08-18)

장면:
- RT-2C Gaussian 단일 반사 synthetic scene
- 100,000 primary ray, 200,000 CAD intersection query
- stored path OFF, summary contribution
- 3회 실행 중앙값

결과:
- scalar dispatch: `2.7691초`, `36,112 primary ray/s`
- batch 256: `3.9080초`, `25,588 ray/s`, `0.709x`
- batch 4,096: `3.8938초`, `25,682 ray/s`, `0.711x`
- batch 65,536: `3.9964초`, `25,022 ray/s`, `0.693x`
- receiver/surface/terminated/flux 전체 exact 일치
- semantic mismatch: `0`

PERF-3B-1 측정 당시 batch dispatch는 sampler의 ray별 generator/yield를
없애고 chunk 단위
dispatch 경계를 만들었지만 Receiver/plan/commit과 `intersect_rays()` 내부는
`python_cpu` reference의 Python scalar 처리를 사용했으므로 end-to-end로는
최선의 4,096 chunk도 scalar 대비 처리량이 약 28.9% 낮고 실행시간은 약
40.6% 길다. 이는 PERF-3B-2 native kernel이 제거해야 할 dispatch overhead
기준이다. PERF-3B-1 후보 중 4,096 chunk가 가장 빨랐으며 Stop 응답성과
향후 GPU launch 비용을 함께 고려해 유지한다.

## PERF-3B-2 native CPU 측정 (2026-08-18)

환경:
- Windows 10, Python 3.13.3
- Numba 0.66.0, llvmlite 0.48.0
- strict `float64`, `fastmath=false`, serial native kernel
- 동일 seed와 frozen ray 배열, 3회 warm 실행 중앙값

실제 CAD intersection micro:
- CAD: `tv_leakage_roi_right_bottom_no_gap.stp`
- triangle: 50,944, ray: 100,000
- Python scalar BVH: `4.4195초`, `22,627 ray/s`
- native scalar 호출: `1.0627초`, `94,101 ray/s`, `4.16x`
- native batch 256: `0.1173초`, `852,575 ray/s`, `37.68x`
- native batch 4,096: `0.0907초`, `1,102,531 ray/s`, `48.73x`
- native batch 65,536: `0.0876초`, `1,141,467 ray/s`, `50.45x`
- face mismatch: `0`, distance bit mismatch: `0`

준비 비용:
- 기존 BVH build: `0.9063초`
- immutable native scene pack: `0.0706초`
- 최초 JIT compile: `1.5052초`
- 최초 native execute: `0.0858초`
- 최초 native cold wall: `1.9288초`

100,000-ray 단일 반사 synthetic end-to-end(모든 case에서 비교를 위해
`intersection_backend="bvh"` 강제):
- Python scalar: `3.1828초`, `31,419 primary ray/s`
- native scalar: `4.6694초`, `0.682x`
- Python reference batch: `4.4098초`, `0.722x`
- native batch 4,096: `3.1531초`, `1.009x`
- receiver/surface/flux semantic mismatch: `0`

별도 `--no-write` 독립 재실행에서는 최대 CAD micro `48.98x`, native batch
end-to-end `0.961x`였다. 즉 wall-time 변동을 포함해도 교차 kernel은 약
49~50배였지만 전체 파이프라인은 baseline 수준이며 `1.20x` gate와는 충분히
떨어져 있다.

교차 커널의 큰 개선이 전체 실행에서 바로 나타나지 않은 이유는 이 synthetic
장면의 교차가 매우 싸고 Receiver 계산, reflection plan, Python 객체 복원과
row-order commit이 실행시간 대부분을 차지하기 때문이다. 더 중요한 실제
PERF-3B-2 측정 당시 사용 조건인 `max_depth >= 2`는 PERF-3B-1 wavefront
대상이 아니었으므로, 그 단계만으로 “백만 ray 수 분” 문제를 해결했다고
판단하지 않았다. 이 범위는 후속 PERF-3B-2A에서 별도로 연결했다.

따라서 native provider는 명시적 개발/benchmark opt-in으로 유지한다. 자동
선택 승격 조건은 실제 ROI end-to-end mismatch `0`, warm 성능 최소 `1.20x`,
기본 Python CPU 회귀 3% 이내, cold start와 배포 크기 수용이다. 현재 optional
module directory 크기는 Numba 약 33.2 MiB, llvmlite 약 116.6 MiB로 합계 약
149.8 MiB다. dist-info, 추가 native library와 archive overhead를 포함한 실제
배포 증가는 이보다 조금 클 수 있다.

별도 수동 기본 CPU 회귀 검증은 동일 프로세스에서 PERF-3B-1 parent와 현재 코드를
순서 교대해 각각 13회 확인했다. 100,000-ray scalar 중앙값은 parent
`2.7708초`, 현재 `2.7824초`였고 runtime 차이는 `+0.42%`, paired mean 차이는
`+0.03%`였다. 결과 payload는 exact 일치했고 Numba import/probe/JIT/native
scene pack은 한 번도 발생하지 않았다.

## PERF-3B-2A multi-bounce wavefront 측정 (2026-08-19)

아래 값은 실제 프로젝트를 같은 seed/geometry로 복원한 warm 반복 측정이다.
기본 `auto` 승격 근거가 아니라 명시적 multi-bounce batch 후보의 성능 gate다.

조건:

- 활성 ROI triangle: `45,167`
- primary ray: `100,000`
- `max_depth=10`
- stored path quota: `500`
- 같은 프로젝트, seed와 geometry

| 실행 | 시간 | Python scalar 대비 | Native scalar 대비 |
| --- | ---: | ---: | ---: |
| Legacy Python scalar 재측정 | `26.1930초` | `1.00x` | `0.43~0.44x` |
| Explicit Numba native scalar | `11.2558~11.4236초` | `2.29~2.33x` | `1.00x` |
| Numba wavefront batch 4,096 | `7.3109초` | `3.58x` | `1.54~1.56x` |
| Numba wavefront batch 1,024 | `7.0649초` | `3.71x` | `1.59~1.62x` |

Stored-path 객체 생성 최적화 전 같은 4,096 wavefront는 `8.8017초`, 최적화
후는 `7.4763초`였다. 실행시간은 약 `15.1%` 줄었고, 100,000개 완료 경로 중
`931`개만 실제 materialize했으며 저장소에 들어갈 수 없는 `99,069`개는
생략했다. 정량 Receiver/contribution 결과와 bounded stored-path 정책은 이
최적화의 영향을 받지 않는다.

현재 wavefront 결과는 같은 seed에서 chunk 크기와 반복 실행에 대해 exact하게
재현됐다. Random draw가 없는 specular 합성 장면은 legacy scalar와 Receiver
grid, flux, contribution, reflection summary와 stored path가 exact 일치했다.
Stochastic/Russian-roulette 장면은 `per_primary_seeded_v1` wavefront 내부에서
chunk/provider exact를 확인했다. Legacy depth-first scalar와 seed 3개씩 비교한
평균 차이는 Receiver hit `-0.92%`, surface hit `+0.33%`, flux `-0.93%`였고
95% CI가 모두 0을 포함했다. 유의한 bias 증거는 없지만 표본이 작아 더 큰
통계 검증 전까지 `auto`는 승격하지 않는다.

최종 결과는 실제 multi-bounce workload에서 native intersection을 wavefront로
연결하는 방향이 유효함을 보여준다. 그러나 1,000,000-ray 선형 환산은 약
`70.7초`다. 따라서 사용자가 요구한 백만 ray와 LightTools 이상 속도를
달성했다고 주장하지 않는다.
Wavefront timing에서 확인한 reflection planning과 ordered commit은 이후
2B/2C/2C-2에서 배열화와 compiled summary reducer로 이어졌다. 남은
prepare/validation/apply 비용과 stochastic planning 범위를 줄인 뒤 같은
active-ray buffer를 CUDA backend에 재사용한다.

## PERF-3B-2B compiled wavefront 측정 (2026-08-19)

실제 ROI canonical은 2A 역사적 표와 별개의 동일 조건 parent 재측정으로
비교했다.

조건:

- 원본/활성 ROI triangle: `50,944 / 45,167`
- primary ray `100,000`, `max_depth=10`, seed `42`
- contribution `summary`, stored path quota `500`
- runtime 기본 chunk `1,024` (batch-size 인자 생략)
- explicit Numba intersection, 기본 `auto` reflection planner
- warm wall-time 중앙값

| 비교 경로 | Wall 중앙값 | PERF-3B-2B speedup |
| --- | ---: | ---: |
| Legacy Python scalar | `26.193초` | `4.984x` |
| 역사적 Numba intersection scalar | 약 `11.42초` | 약 `2.173x` |
| PERF-3B-2A parent 동일 1,024 조건 | `7.0649초` | `1.344x` |
| PERF-3B-2B final default 1,024 | `5.2553초` | `1.000x` |

최종 2B canonical과 2A parent 모두 1,024 chunk이므로 `7.0649초`를 direct
speedup 분모로 사용한다. Final-source 5회 sweep의 1,024 p50 `5.1601초`와
final-default 3회 p50 `5.2553초`의 차이 `1.84%`는 실행 변동 범위다.

PERF-3B-2B 결과는 Receiver hit `12,652`, surface hit `225,482`, terminated
`87,348`, Receiver flux `0.040176617410112817`, stored path `500`, intersection
logical query row `309,119`이다. 현재 2B wavefront의 반복 실행과
`auto`/`python_cpu` planner 사이 ordered payload는 exact 일치했다. Path는
`931`개를 materialize하고 quota에 들어갈 수 없는 `99,069`개를 생략했다.
Mixed stochastic 장면의 legacy/Numba scalar 값은 timing reference이며 기존
statistical parity 계약상 이 exact 비교 범위에 포함하지 않는다.

Canonical planner가 `auto`라 native planner attempt는 `0`이다. 실제 모델의
모든 surface도 mixed scatter라 explicit native에서 Python sidecar 대상이다.
따라서 parent 대비 `1.344x`는 batch surface geometry materialization과 O(1)
stored-path quota의 효과이며 compiled planner의 실제 ROI speedup으로 해석하지
않는다.

Stable-source paths-on 교대 재측정에서 p50은 1,024 `5.1601초`, 4,096
`5.1863초`로 차이가 약 `0.51%`뿐이었다. 처리량은 동률로 판단하고,
별도 synthetic depth-10 `tracemalloc`의 Python allocation peak는 약
`9.65 MiB`에서 `37.64 MiB`로 늘고 Stop 원자 단위가 4배 커진다. 이 값은 실제
ROI process RSS가 아니라 scratch scaling 참고값이다. 따라서 runtime 기본은
memory와 Stop 응답성이 유리한 1,024를 유지한다.

Deterministic specular depth-10 synthetic는 10,000 primary ray, chunk 4,096,
warm p50로 Python/Numba planner를 비교했다.

| Scenario | Python planner | Numba planner | Speedup |
| --- | ---: | ---: | ---: |
| Summary, paths off | `1.304850초` | `1.113563초` | `1.172x` |
| Summary, paths on | `1.476219초` | `1.189662초` | `1.241x` |
| Detailed, paths off | `1.316303초` | `1.205550초` | `1.092x` |
| Detailed, paths on | `1.390839초` | `1.290724초` | `1.078x` |

네 scenario 모두 semantic mismatch `0`이다. 지원 범위에서는 compiled planner가
이득이지만 실제 mixed ROI, optional Numba/JIT와 배포 크기 gate를 함께 만족하지
않았으므로 기본 `auto`는 Python planner/scalar CPU를 유지한다.

현재 100,000-ray `5.2553초`의 1,000,000-ray 단순 선형 환산은 약
`52.6초`다. PERF-3B-2C에서 Python ray-state object를 대체할 SoA와 compact
event tape 경계를 구현했지만 기본 승격 기준은 통과하지 못했다. 다음 단계는
exact ordered reducer를 compile하고, `counter_rng_v2`로 stochastic planner
범위를 넓힌 뒤 같은 buffer와 whole-depth fallback 계약을 CUDA에 재사용하는
것이다.

## PERF-3B-2C-1 Event-tape v2 측정 (2026-08-19)

이번 단계는 같은 depth-major reflection plan을 `object_reference`와
`soa_event_tape` pipeline으로 counterbalanced 측정한다. 비교 시 seed, chunk,
intersection provider, planner, contribution mode와 path quota를 고정하며
semantic mismatch와 event/tape peak bytes를 함께 기록한다.

조건:

- 원본/활성 ROI triangle `50,944 / 45,167`
- primary ray `100,000`, `max_depth=10`, seed `42`
- summary, stored paths `500`, chunk `1,024`
- explicit Numba intersection, planner `auto`
- pipeline별 warmup 1회 뒤 3회 측정, 순서 `O,S,S,O,O,S` counterbalanced,
  source hash stable

| Pipeline | Wall p50 | Wall p95 | Primary ray/s p50 | 1M 선형 환산 |
| --- | ---: | ---: | ---: | ---: |
| `object_reference` | `5.232795초` | `5.288968초` | `19,110.25` | `52.33초` |
| `soa_event_tape` | `5.121246초` | `5.130226초` | `19,526.50` | `51.21초` |

SoA v2는 object-reference 대비 `1.021782x`, wall `2.132%` 개선됐다. State init
`0.084956초`와 advance `0.024213초`는 object state build `0.493646초`보다
작았다. Tape append `0.209180초`, seal `0.102317초`, 그 안의 public strict
validation `0.060045초`, Python reducer replay `1.052167초`와 stored-path hydrate
`0.024338초`가 추가됐다. Plan 전체는 `2.588661 -> 2.960907초`, commit은
`0.859558 -> 1.094071초`였다. State init은 state build와 같은 구간이고,
state advance/append/seal은 plan에, validation은 seal에, replay/hydrate는
commit에 포함되는 nested metric이므로 합산하지 않는다. Intersection/native
execute도 `0.476005/0.382826초`와 `0.482072/0.388773초`로 같은 범위였다.

두 pipeline은 Receiver `12,652`, surface `225,482`, terminated `87,348`, flux
`0.040176617410112817`, query `309,119`, path `500`, materialized/skipped
`931/99,069`와 semantic/grid/contribution/path hash가 exact했다. SoA
event/reducer count는 각각 `225,482`, primary-chunk paths-on tape peak 최대값은
`680,048 bytes`, run copy 회계는 `29,407,112 bytes`다. 별도 source-stable 10k
one-run A/B에서 paths-off/on peak는 `271,080 / 643,800 bytes`, copy 회계는
`1,131,996 / 2,952,580 bytes`였다. 이 tape-owned byte를 process RSS나 object
graph 전체 memory와 직접 비교하지 않는다. 실제 ROI는 mixed stochastic이므로 exact 범위는 같은
`per_primary_seeded_v1`의 두 wavefront pipeline 사이이며 legacy scalar에는
기존 statistical parity를 적용한다.

Provider는 여섯 measured run 모두 effective `numba_cpu`, `native_used=true`,
attempt/success `1,078/1,078`, native success row `309,119`, intersection fallback
`0`이었다. Planner `auto`는 effective `python_cpu`, logical/Python-sidecar row
`225,482/225,482`, native attempt `0`, fallback `0`이었다. 따라서 교차 성능은
실제 native intersection 실행이지만 compiled reflection planner의 speedup은
포함하지 않는다.

측정된 `1.021782x`는 자동 승격 gate `>= 1.05x`에 못 미치므로
`wavefront_pipeline="auto"`는 `object_reference`를 유지한다. Actual-event CSR은
2C-2 compiled reducer와 후속 CUDA의 메모리·데이터 경계이며, 1M 선형 환산
`51.21초`로 목표 달성을 주장하지 않는다. 재현 결과는 git-ignored
`outputs/perf3b2c_soa_event_tape/actual_roi_summary.json`에
`perf3b2c_actual_roi_comparison_v1`로 기록했다. Artifact SHA256은
`ef2ad80346d7e1ea44c00fc9cd19be0cfb75c9da00362231920782c486c9ad5e`,
benchmark script SHA256은
`89b223a2c128f83d1cfc76c5f9dee1e9aa8aee7cf5f1fb41f2ad5859c10cb783`다.

## PERF-3B-2C-2 Compiled reducer 측정 (2026-08-19)

2C-1과 같은 actual ROI, SoA tape, intersection/planner 조건에서 reducer만
`python_cpu`와 `numba_cpu`로 바꿨다. Reducer별 10k warmup 뒤 3회씩
`P,N,N,P,P,N` counterbalanced 측정했고 source hash는 전후 동일했다.

| Reducer | Wall p50 | Wall p95 | Primary ray/s p50 | 1M 선형 환산 |
| --- | ---: | ---: | ---: | ---: |
| `python_cpu` | `5.094436초` | `5.128807초` | `19,629.26` | `50.94초` |
| `numba_cpu` | `4.643004초` | `4.697531초` | `21,537.78` | `46.43초` |

Native는 p50 `1.097228x`/wall `8.861%`, p95 `1.091809x`/wall `8.409%`
개선했다. Reducer replay는 `1.062883 -> 0.443459초`(`2.3968x`), commit은
`1.101820 -> 0.643344초`(`1.7126x`)였고 plan은
`2.959407 / 2.962245초`로 같은 범위였다.

Native p50 내부는 prepare `0.174618초`, dispatch `0.310282초`, kernel execute
`0.021806초`, result validation `0.237018초`, apply `0.133177초`, path stage
`0.023353초`다. 이 값들은 nested timing이며 서로 단순 합산하지 않는다. 현재
kernel보다 prepare/validation/public object apply가 더 큰 후속 최적화 대상이다.

Receiver/surface/terminated `12,652 / 225,482 / 87,348`, flux
`0.040176617410112817`, path `500`, materialized/skipped `931/99,069`는 같았다.
Native tape/primary/event는 `98 / 100,000 / 225,482`, attempt/success `98/98`,
fallback `0`이었다. Seven semantic/hash family, grid/contribution/path와 ordered
float bits는 warmup/measured 전체에서 exact했다. Paths-off 10k quick도
`1.081299x`, exact였고 detailed synthetic은 정상 Python 선택/attempt `0`였다.

Sampled RSS peak delta p50 `4,210,688 / 3,735,552 bytes`는 allocator와 측정
순서에 민감하므로 memory 우위로 주장하지 않는다. Tape peak/copy는 두 reducer
모두 `680,048 / 29,407,112 bytes`로 같다.

Warmup에서 reducer cold JIT는 `2.382357초`였다. 따라서 warm gate
`>= 1.05x`를 통과했어도 짧은 단발 실행, optional Numba/llvmlite 배포와
GPU·Numba가 없는 PC의 CPU 무회귀 원칙 때문에 `wavefront_reducer="auto"`는
Python/no-probe를 유지한다. Explicit native는 반복 실행에서 사용할 수 있다.

Actual artifact SHA256은
`04bb4514a3a5909a5f8afbc551cecd4de3c84b70c11cada6d9335f7ec5dcf648`, final
audit SHA256은
`feacdb1acbb7e757d4690147bea8bf0e9a6b75439cc81b4573faa43e1877846a`다. 실제
사용자 input은 hash만 기록하고 repository fixture로 추가하지 않는다.
Actual 전용 harness SHA256은
`f11985a62911d0eb47312adb46990dd8c0bee6c11dc503c78667748ba49123e8`, repository
benchmark SHA256은
`0c7308f1a7effffde4b3efb534181d4dfbd369247ae5625731eb2acc7e688834`다.

## PERF-3C CUDA 측정 (2026-08-20)

Actual CAD 50,944 triangles와 frozen 100,000 rays의 warm intersection micro에서
CUDA 65,536 p50은 `0.012916초`(`7.743M ray/s`)로 Numba CPU 8,192
`0.089381초`보다 `6.920x` 빨랐다. Face/tolerance mismatch는 모두 `0`, 최대
distance absolute error는 `2.8422e-14`다. CUDA 8,192/16,384 speedup은
`2.650x / 4.094x`였다. 최초 cold 8,192 실행은 `0.942792초`이고 JIT
`0.852086초`를 포함한다.

모든 ray가 depth 10까지 유지되는 synthetic 100,000-primary control은 Numba
CPU/GPU p50 `1.787338 / 1.817925초`, GPU `0.983175x`였다. 즉 actual CAD
intersection micro 가속만으로 모든 end-to-end workload가 빨라진다고 주장하지
않는다. Count/grid/summary exact, path tolerance mismatch와 fallback은 `0`이다.

45,167 active-triangle actual ROI, depth 10, summary, paths 500의 실제
1,000,000-primary source-freeze isolated warm raw는
`7.277951 / 7.270346 / 8.085747초`, p50/p95는
`7.277951 / 8.004967초`, p50 처리량은 `137,401 primary ray/s`다.
Receiver/surface/terminated는 `126,609 / 2,250,471 / 873,391`, flux는
`0.03998454755283727`, path는 `500`이며 세 run outcome이 exact했다.
Representative p50 run의 logical intersection은 `176 / 3,085,763` batch/ray다.
그중 CUDA는 `92 / 2,710,197`, hybrid Numba CPU는 `84 / 375,566`이고 모두
attempt=success, hard fallback은 `0`이다. Planner `176/176`, reducer `16/16`
성공과 fallback `0`을 기록했다. Requested/effective provider는
`gpu_cuda / mixed`이며, 이는 정상 hybrid를 반영한다. Representative
intersection/GPU kernel/hybrid CPU는 `1.578990 / 0.822200 / 0.507024초`,
plan/commit/wavefront는 `2.078457 / 1.387784 / 6.988246초`다.

GPU 기본 chunk 65,536은 launch/transfer를 줄이지만 memory와 Stop 원자 단위를
늘린다. Actual 1M sampled RSS delta는 `57,720,832 bytes`, tape-owned peak/copy는
`40,527,016 / 293,678,488 bytes`다. Tape 회계는 VRAM peak가 아니며 CUDA
workspace도 run 중 자동 축소하지 않는다. `<8,192` hybrid가 작은 tail wave의
launch를 피하지만, VRAM peak와 실제 Stop latency는 후속 계측 대상이다.

8 seed × 512-ray `counter_rng_v2` statistical gate는 legacy stream 대비 Gaussian
fraction absolute delta `0.0167253`(gate `0.05`), emitted-flux relative delta
`5.3583%`(gate `10%`)로 통과했다. Counter stream은 chunk/provider/reorder exact지만
legacy stream과 개별 ray bit-exact가 아니라 통계 비교 대상이다.

재현 script는 `scripts/benchmark_perf3c_gpu_cuda.py`, actual CAD micro/control
artifact는 `outputs/perf3c_gpu_cuda/summary.json`이다. 각각 SHA256은
`a9eb801d1931ba3cb5ff9549d3631a001e56f7884bdaf6291c17a390dac952f5`,
`e2ced20f63ea8a654b3db4cae65d32b35790e6aa73b4c96b917cc293e8ffb527`다.
Actual ROI 1M artifact SHA256은
`13ca76ce6c4e8129ae7b5dfefbadaca8c20d06884b7264d0c60a5e65812fef2e`다.
상세 계약과 해석 제한은
`docs/changes/2026-08-20_perf3c-strict-fp64-cuda-wavefront.md`를 따른다.

## PERF-3D Host-overhead 측정 (2026-08-20)

PERF-3C와 같은 actual ROI 1M, depth 10, paths 500의 source-frozen warm raw는
`5.715687 / 5.541795 / 5.208224초`, p50/p95는
`5.541795 / 5.698298초`, 처리량은 `180,447 primary ray/s`다. PERF-3C p50
`7.277951초` 대비 `1.3133x`, latency `23.855%` 개선됐다.

Receiver/surface/terminated `126,609 / 2,250,471 / 873,391`, flux
`0.03998454755283727`, stored paths `500`과 ordered semantic hash는 PERF-3C와
세 run 모두 exact했다. Intersection/planner/reducer hard fallback은 `0`이다.

Representative Receiver/plan/commit/wavefront는
`0.216118 / 1.576959 / 1.145978 / 5.274202초`다. Intersection은
`1.985698초`로 PERF-3C representative `1.578990초`보다 불리했으므로 CUDA
intersection 개선으로 해석하지 않는다. Host overhead 감소가 전체 wall 개선을
만들었다.

Run accumulator는 `16 tape / 1,000,000 primary / 2,250,471 event`를 유지하고
마지막에 `1회 / 0.000637초` flush했다. Payload는 첫 65,536-primary tape만 full,
뒤 15개 tape는 omitted였고 copy accounting은
`293,678,488 -> 124,395,352 bytes`(`-57.642%`)다.

100k counterbalanced p50은 PERF-3C parent/PERF-3D per-tape/retained
`0.831689 / 0.772222 / 0.734558초`로 retained가 parent 대비 `1.1322x`, 같은
PERF-3D per-tape 대비 `1.0513x`였다. 성능 threshold는 report-only다.

CPU default paired p50은 actual `-1.80%`, synthetic `+0.812%`로 `3%`
no-regression gate 안이었다. Semantic/count/path exact, fresh-process
Numba/CUDA/native no-probe도 통과했다. 최종 Python suite는
`237 passed, 279 subtests passed`다.

상세 계약, artifact와 해석 제한은
`docs/changes/2026-08-20_perf3d-host-overhead-run-accumulator.md`를 따른다.
