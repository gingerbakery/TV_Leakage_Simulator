# PERF-4C GPU 결과 누적기 검증 보고서

## 결론

PERF-4C는 PERF-4B의 전체 event tape 다운로드와 CPU ordered reducer를 일반 summary
실행에서 제거했다. RTX 3070 실기기 고정 workload에서 이산 결과는 모두 exact였고,
최대 absolute error는 `5.239e-10`으로 `1e-9` 계약을 통과했다. CUDA/host fallback은
발생하지 않았다.

## 환경과 판정 증거

| 항목 | 결과 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 3070 |
| Compute capability | 8.6 |
| Numba | 0.66.0 |
| `available` / `strict_float64` | `true` / `true` |
| `kernel_executed` / `kernel_verified` | `true` / `true` |
| Preflight scope | `production_ray_bvh` |
| Accumulator contract | `strict_float64_gpu_summary_accumulator_v1` |

## Canonical 100,000-Ray 결과

동일 process와 장면에서 cold 1회 후 counterbalanced warm 3회를 측정했다. 비교
기준은 PERF-4B resident trace + host ordered reducer이고, 후보는 같은 trace에
PERF-4C GPU accumulator를 적용한 경로다.

| Workload | PERF-4B warm p50 | PERF-4C warm p50 | 개선 | 전송량 감소 | 최대 절대오차 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stochastic depth 2 | 0.113087 s | 0.060857 s | 1.858x | 10,003,400 → 7,504 B (99.925%) | 7.84e-14 |
| Trapped depth 10 | 0.787021 s | 0.102006 s | 7.715x | 42,400,016 → 9,344 B (99.978%) | 5.239e-10 |

두 workload 모두 이산 차이 `0`, strict float64 tolerance 통과, accumulator success
2 chunk, resident fallback `0`을 기록했다.

## 1,000,000-Ray 보조 측정

확장성 확인용으로 warm 1회만 추가 측정했다. 반복 수가 1이므로 canonical 판정값이
아니며 방향성 확인에만 사용한다.

| Workload | PERF-4B | PERF-4C | 개선 | 전송량 감소 |
| --- | ---: | ---: | ---: | ---: |
| Stochastic depth 2 | 1.032046 s | 0.464796 s | 2.220x | 100,002,288 → 60,032 B (99.940%) |
| Trapped depth 10 | 7.362592 s | 0.984763 s | 7.477x | 424,000,128 → 74,752 B (99.982%) |

## 1억 Ray 환산 주의

100,000-Ray canonical p50의 단순 선형 환산은 stochastic depth 2 약 `60.9초`,
trapped depth 10 약 `102.0초`다. 1,000,000-Ray 보조 측정 환산은 각각 약
`46.5초`, `98.5초`다.

이 값은 실제 1억 Ray TV CAD 측정이 아니다. CAD triangle 수, Receiver 희귀 hit,
열 throttling, VRAM, chunk 수, UI 및 결과 저장 비용을 포함하지 않으므로 목표 달성
보장값으로 사용하지 않는다.

## 회귀 검증

- PERF-4C focused: `4 passed`
- PERF-4B focused: `5 passed`
- Reducer/performance matrix: `25 passed, 28 subtests passed`
- 전체 Python suite: `309 passed, 445 subtests passed`
- `git diff --check`: 오류 없음(LF/CRLF 경고만 존재)

## 판정

- PERF-4C 구현 Gate: 통과
- CPU ordered reducer 대비 수치 정합: 통과
- 전체 event tape 제거 및 compact output: 통과
- 실제 회사 TV ROI 1억 Ray·장시간 안정성 Gate: 미검증, PERF-4D/운영 검증에서 수행
