# PERF-4A / PERF-4B 실기기 성능 보고서

## 측정 환경

- 일자: 2026-08-25
- GPU: NVIDIA GeForce RTX 3070
- Compute Capability: 8.6
- Python: 3.13.3
- Numba: 0.66.0
- primary Ray: workload당 100,000
- chunk: 65,536
- warm 반복: 3회
- Monte Carlo: `cpu_gpu_deterministic_batch_v1`
- CUDA BVH: `strict_float64_bvh_v1`
- Resident provider: `strict_float64_resident_wavefront_v1`

## PERF-4A 기준선

PERF-4A는 `host_roundtrip`으로 고정했다.

| Workload | 반사 | Warm p50 | Warm p95 | 1억 Ray 선형 환산 |
| --- | ---: | ---: | ---: | ---: |
| Face direct | 1 | 0.831초 | 0.838초 | 13.9분 |
| Stochastic two-bounce | 2 | 0.174초 | 0.181초 | 2.9분 |
| Trapped corridor | 10 | 1.296초 | 1.306초 | 21.6분 |

세 workload 모두 CUDA success batch가 존재했고 hard fallback은 0회였다.

## PERF-4B 결과

| Workload | Host p50 | Resident p50 | 개선 | Host 1억 | Resident 1억 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stochastic two-bounce | 0.165초 | 0.117초 | 1.41배 | 2.8분 | 2.0분 |
| Trapped corridor depth 10 | 1.297초 | 0.827초 | 1.57배 | 21.6분 | 13.8분 |

두 workload 모두 다음 조건을 만족했다.

- `compute_execution_state=gpu_active`
- resident 성공 batch 2개, fallback 0회
- host-roundtrip 대비 공개 semantic payload exact
- Receiver/surface/terminated count exact
- source hash 측정 전후 동일

Depth-10 대표 run은 100,000 primary Ray에서 logical intersection row
1,100,000개를 처리했다. Resident 내부 계측은 kernel 약 0.059초, output download
약 0.021초, host tape build 약 0.113초였다. 전체 0.827초에는 ordered reducer,
입력 생성, Python 결과 구성 등 외부 시간이 포함된다.

## 정확도 이중 검증

1. 기존 CPU ↔ GPU host-roundtrip 100,000-Ray gate:
   - face direct exact
   - stochastic two-bounce exact
   - 모든 case `passed=true`
2. GPU host-roundtrip ↔ GPU resident 100,000-Ray gate:
   - 두 workload semantic exact
3. CPU ↔ GPU resident 8,192-Ray 확률 반사 회귀:
   - 이산 차이 0
   - 최대 상대오차 약 `4.1e-16`
   - 최대 ULP 2, 허용 기준 8 이하
4. 강제 resident kernel 실패:
   - 같은 chunk host-roundtrip 1회 replay
   - 공개 결과 exact

## 판정

PERF-4A 고정 기준선과 PERF-4B GPU 상주형 1차 구현은 통과했다. Depth-10 1억 Ray
선형 환산은 약 13.8분으로 기존 21.6분보다 개선됐지만 10분 목표에는 아직
도달하지 않았다. 다음 병목은 GPU 계산 자체보다 event tape 생성/다운로드와 CPU
ordered reducer다. 따라서 다음 우선순위는 PERF-4C GPU Receiver/Heatmap 및
contribution accumulator다.

이 환산은 synthetic all-survive 장면의 선형 추정이며, 실제 TV CAD triangle 수,
Receiver hit rate, VRAM, thermal throttling과 5% 통계 error를 보장하지 않는다.
