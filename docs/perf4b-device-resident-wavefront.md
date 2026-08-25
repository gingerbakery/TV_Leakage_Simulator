# PERF-4B GPU 상주형 Wavefront 계약

## 목적

기존 GPU 경로는 반사 depth마다 GPU BVH 교차를 실행한 뒤 결과를 CPU로 내려받아
Receiver 판정, 광학 속성 조회, 반사 방향 생성과 종료 판정을 수행했다. 반사가
10회 이상이면 같은 Ray 상태가 CPU와 GPU 사이를 반복 이동한다.

PERF-4B는 한 primary Ray를 CUDA thread 하나가 맡아 종료될 때까지 다음 작업을
GPU에서 연속 수행한다.

1. Receiver plane 교차 판정
2. strict-float64 BVH traversal
3. face optical property 조회
4. Specular/Lambertian/Gaussian/Mixed 반사 방향 생성
5. 반사율 감쇄와 threshold/Russian roulette 종료 판정

## 구현 계약

- Provider: `gpu_cuda_resident_wavefront`
- Provider 계약: `strict_float64_resident_wavefront_v1`
- 상태 배치: `primary_thread_resident_masked_v1`
- Monte Carlo 계약: `cpu_gpu_deterministic_batch_v1`
- 지원 반사 depth: `0~32`
- 기본 chunk: GPU 실행 시 `65,536 primary Ray`
- 연산 정밀도: geometry, power, direction 모두 `float64`

이번 1차 구현은 depth별 global compaction kernel을 반복 실행하지 않는다. 대신
primary thread가 device 안에서 depth loop를 수행하고 종료된 thread가 즉시 빠지는
방식이다. 이 구조는 depth별 host/device 왕복과 CPU reflection planner를 제거하며,
현재 장면처럼 Ray 하나가 다음 Ray 하나로 이어지는 경로에 적합하다.

## 선택 조건

프로덕션 `run_direct_ray_trace()`가 모두 `auto`이고 사용자가
`compute_backend="gpu_cuda"`를 선택하면 다음 조건에서 자동 사용한다.

- batch intersection
- CUDA BVH provider
- `soa_event_tape`
- `counter_rng_v2`
- `max_depth > 1`

직접 진단할 때는 runtime-only `wavefront_residency`를 사용할 수 있다.

| 값 | 의미 |
| --- | --- |
| `auto` | 위 조건에 따라 GPU resident 또는 host-roundtrip 선택 |
| `host_roundtrip` | PERF-4A 기준 depth별 GPU/CPU 왕복 경로 |
| `gpu_resident` | PERF-4B CUDA 상주형 경로 요청 |

이 값은 `.bitsam` 프로젝트 데이터 계약에 저장하지 않는 진단/성능 선택 값이다.

## 출력과 기존 결과 호환

GPU는 primary-major event 배열을 한 번 내려받고 기존
`ordered_primary_event_tape_v3`로 seal한다. 그 뒤 기존 ordered reducer가 Receiver
grid, contribution, reflection summary와 저장 path를 원 primary 순서로 집계한다.
따라서 공개 결과 스키마와 누적 순서는 기존 경로를 유지한다.

`gpu_accumulator="host"` 기준선에서는 최종 scalar 합계만이 아니라 event tape가
CPU로 내려온다. 이것은 기존 결과 계약을 안전하게 유지하기 위한 4B의 의도적인
경계다. 프로덕션 `auto` summary 경로에서는 PERF-4C가 event tape를 device에서
직접 집계하며, 상세 계약은 `docs/perf4c-gpu-accumulator.md`를 따른다.

## 실패 처리

- CUDA 부재 또는 strict-float64 미지원: resident provider를 사용하지 않는다.
- 입력 upload, kernel, output validation, BVH stack overflow 또는 tape seal 실패:
  해당 primary chunk 전체를 기존 host-roundtrip 경로로 정확히 한 번 재실행한다.
- 첫 resident 실패 뒤에는 run-local circuit breaker를 열어 이후 chunk를
  host-roundtrip으로 처리한다.
- 실패한 resident 결과를 일부만 누적하지 않는다.

결과의 다음 항목으로 실제 사용 여부를 판정한다.

- `wavefront_residency=gpu_resident`
- `gpu_resident_wavefront_contract=strict_float64_resident_wavefront_v1`
- `gpu_resident_wavefront_success_count > 0`
- `gpu_resident_wavefront_fallback_count = 0`
- `compute_execution_state=gpu_active` 또는 `gpu_mixed`

## 정확도 계약

결정 결과는 완전 일치해야 한다.

- Receiver/surface/terminated count
- face, component, material, reflection lobe와 depth
- Receiver cell hit count
- contribution 구조와 key 순서

CPU와 CUDA의 `sin`, `cos`, `log`, `sqrt` 구현은 bit-identical하지 않을 수 있다.
확률 반사 수치에는 아래 엄격 조건을 적용한다.

- abs tolerance `1e-12`
- relative tolerance `1e-12`
- 최대 ULP distance `8`
- 위 조건과 별개로 모든 이산 결정은 exact

`scripts/perf4_accuracy.py`가 이 계약을 구현한다. 8,192-Ray mixed/Lambertian
실기기 회귀에서는 이산 차이 `0`, 최대 상대오차 약 `4.1e-16`, 최대 ULP `2`였다.

## 실행

PERF-4A 기준선:

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4a_target_workloads.py `
  --backend gpu_cuda --rays 100000 --repeats 3
```

PERF-4B 비교:

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4b_resident_wavefront.py `
  --rays 100000 --repeats 3
```

결과 JSON은 각각 `outputs/perf4a_target_workloads/summary.json`,
`outputs/perf4b_resident_wavefront/summary.json`에 생성되며 git에는 포함하지 않는다.

## 현재 한계와 다음 단계

- 이 문서의 4B 기준선은 event tape를 CPU로 내려받아 ordered reducer로 집계한다.
  프로덕션 summary 경로의 이 병목은 PERF-4C에서 제거했다.
- 최대 depth 전체 크기의 event workspace를 chunk마다 확보하므로 depth가 커지면
  VRAM과 다운로드량이 선형 증가한다.
- 실제 사내 TV ROI 대표 장면은 아직 PERF-4A 고정 장면으로 등록하지 않았다.
- 1억 Ray·10회 반사 10분 목표의 다음 단계는 PERF-4D kernel/workspace 추가 융합과
  실제 TV ROI 장시간 검증이다.
- 희귀 Receiver hit의 5% error 달성 Ray 수 자체를 줄이는 importance sampling과
  Next Event Estimation은 PERF-4E에서 별도로 다룬다.
