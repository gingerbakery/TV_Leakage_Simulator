# GPU 1억 Ray·다회 반사 성능 타당성 검토

## 목적

- 목표 조건: `100,000,000` primary Ray, 최대 반사 `10회 이상`, Receiver 통계
  Error target `5% 이하`
- 목표 시간은 아직 확정되지 않았으므로 `5/10/15/20/30분` 기준으로 필요한
  처리량과 현재 구현의 차이를 비교한다.
- Error target 달성 여부는 장면의 Receiver hit rate에 좌우된다. 따라서 이 문서의
  시간 환산은 **1억 Ray를 실제로 처리하는 시간**이며, 1억 Ray가 모든 장면에서
  5% 수렴을 보장한다는 뜻이 아니다.

## 측정 환경

- GPU: NVIDIA GeForce RTX 3070, 8 GB, compute capability 8.6
- CPU: Intel Core i7-10700
- CUDA 계약: strict FP64 BVH
- 실행 구조: CUDA BVH intersection + CPU Numba reflection planner/reducer
- workload: 1,000,000 primary Ray가 depth 10까지 모두 생존하는 보수적 synthetic
  control
- chunk: 65,536 Ray
- stored path payload: omitted, run accumulator 사용

## 현재 처리량

Warm 측정에서 1,000,000 primary Ray와 11,000,000 logical intersection row 처리에
`13.14~14.48초`, 즉 약 `69,000~76,000 primary Ray/s`가 소요됐다. 현재 구조를
단순 선형 환산하면 1억 primary Ray는 약 `21.9~24.1분`이다.

별도 실제 ROI 이력에서는 1,000,000 primary Ray가 `5.54초`였지만 평균 logical
intersection이 primary당 약 `3.09회`였다. 이 조건을 선형 환산하면 약 `9.2분`이나,
모든 Ray가 10회 반사하는 갇힌 구조에는 적용할 수 없다.

| 목표 시간 | 필요한 primary Ray/s | 현재 대비 필요 가속 |
| ---: | ---: | ---: |
| 30분 | 55,556 | 현재 범위에서 가능 |
| 20분 | 83,333 | 약 `1.1~1.2x` |
| 15분 | 111,111 | 약 `1.5~1.6x` |
| 10분 | 166,667 | 약 `2.2~2.4x` |
| 5분 | 333,333 | 약 `4.4~4.8x` |

첫 실행은 CUDA/Numba JIT 때문에 warm 실행보다 느릴 수 있다. 실제 제품 목표는
동일 앱 세션의 두 번째 이후 실행을 기준으로 하되 cold time도 별도로 기록한다.

## 병목 분석

1,000,000 primary·depth 10 warm profile 한 회의 대표 결과는 다음과 같다.

| 구간 | 시간 |
| --- | ---: |
| CUDA 포함 전체 intersection dispatch | `1.254초` |
| CUDA kernel | `0.570초` |
| Host→Device input upload | `0.413초` |
| Device→Host output download | `0.124초` |
| Receiver 후보 계산 | `0.959초` |
| Reflection planning | `4.871초` |
| Ordered contribution commit/reduce | `5.941초` |
| 전체 wavefront | `14.207초` |

Reflection planning과 commit/reduce가 전체 wavefront의 약 76%다. 현재 GPU는 BVH
교차만 가속하고 각 depth마다 Ray state와 교차 결과를 CPU로 왕복시킨다. 1M·depth
10에서 event tape logical copy는 약 `416 MB`이며, 1억 Ray 선형 규모에서는 수십 GB
수준의 host memory traffic이 된다. 따라서 CUDA intersection kernel만 추가 튜닝해서
10분 이하를 안정적으로 달성하기는 어렵다.

## 이론적 가능성

- **30분 이하:** 현재 RTX 3070 보수적 control에서도 가능하다.
- **20분 이하:** chunk/host overhead 개선만으로 접근 가능하지만 실제 대형 CAD에서
  보장하려면 대표 장면 benchmark가 필요하다.
- **10분 이하:** 이론적으로 가능하지만 Ray state, Receiver test, reflection sampling,
  compaction과 결과 집계를 GPU에 유지하는 device-resident wavefront가 필요하다.
- **5분 이하:** RTX 3070 기준으로는 도전적이다. 완전 fused kernel, 더 빠른 traversal,
  희귀 Receiver hit를 위한 importance sampling, 또는 상위 GPU가 함께 필요할 가능성이
  높다.

GPU kernel 자체만 놓고 보면 현재 synthetic 11M intersection row를 약 `0.57초`에
처리했다. 다만 실제 45k triangle ROI에서는 traversal이 더 비싸므로 이 수치를 제품
장면의 하한 시간으로 직접 사용하면 안 된다. 그래도 전체 시간의 대부분이 GPU kernel
밖에 있다는 점은 2배 이상의 구조적 가속 여지가 있음을 뜻한다.

## 권장 가속 순서

### PERF-4A: 고정 성능 계약과 대표 장면

- 사내 대표 TV ROI 3종을 익명화하거나 hash 고정 benchmark로 등록한다.
- `primary Ray`, 실제 `intersection row`, 평균/최대 bounce, triangle 수, Receiver hit
  rate, cold/warm 시간을 함께 기록한다.
- 기준 조건은 summary mode, stored paths 500, auto convergence off로 고정한다.

### PERF-4B: Device-resident wavefront

- origin, direction, energy, depth, source face/component ID를 GPU에 유지한다.
- depth마다 전체 배열을 CPU로 가져오지 않고 GPU에서 active Ray compaction을 수행한다.
- Receiver plane test, reflection lobe 선택, 새 direction 생성과 energy termination을
  CUDA kernel로 이동한다.
- CPU에는 progress와 최종 집계만 전달한다.

### PERF-4C: GPU Receiver/Heatmap accumulator

- Receiver hit count, flux, squared flux와 Heatmap grid를 GPU에서 누적한다.
- contention을 줄이기 위해 block-local accumulator 후 최종 reduce를 검토한다.
- stored path는 최대 quota만 reservoir/selected copy하고 전체 event tape 생성을 피한다.

### PERF-4D: Fused traversal/shading과 대체 backend

- intersection → material lookup → reflection sampling → next-wave append를 하나 또는
  소수 kernel로 결합한다.
- Numba 한계를 넘으면 C++/CUDA extension, CuPy RawKernel 또는 NVIDIA OptiX 후보를
  비교한다.
- OptiX/RTX 또는 mixed precision을 사용할 때는 micron gap miss를 막기 위해 AABB를
  보수적으로 확장하고 최종 triangle hit를 FP64로 재검증하는 방식을 우선 검토한다.

### PERF-4E: 필요한 Ray 수 자체 감소

- Receiver-directed importance sampling/Next Event Estimation을 도입한다.
- emitter/surface BRDF sampling과 Receiver-directed sampling을 MIS로 결합해 bias 없이
  희귀 Receiver hit의 분산을 줄인다.
- Auto convergence는 `1→2→4→8` 전체 재실행 대신 이전 sample을 누적 재사용한다.
- 이 단계는 raw GPU speedup보다 5% Error 달성에 필요한 primary Ray 수를 더 크게
  줄일 수 있으나 장면별 검증이 필요하다.

## 정확도 Gate

- 모든 가속 단계는 `cpu_gpu_deterministic_batch_v1` 계약을 유지한다.
- CPU/GPU hit count, receiver grid, Peak/Mean/Flux와 contribution summary를 동일 sample
  기준으로 비교한다.
- 순수 FP32 전환은 허용하지 않는다. Micron gap 장면에서 FP32 candidate traversal을
  도입하면 FP64 reference와 topology miss gate를 별도로 둔다.
- 성능 개선과 물리 모델 보정은 분리한다. 5% Error는 Monte Carlo 표본 오차이며 소재,
  광원과 절대 nit 보정 오차를 포함하지 않는다.

## 결론

1억 Ray·10회 반사에서 **20~30분은 현재 구조의 연장선**, **10분 이하는 GPU 상주형
파이프라인 전환 목표**, **5분 이하는 상주형 pipeline과 importance sampling을 함께
적용하는 도전 목표**로 분류한다. 다음 구현 우선순위는 PERF-4A 대표 장면 성능 계약과
PERF-4B device-resident wavefront다.
