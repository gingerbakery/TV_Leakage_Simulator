# PERF-4D Compact Workspace 검증 보고서

## 환경

- 날짜: 2026-08-25
- GPU: NVIDIA GeForce RTX 3070, compute capability 8.6
- Python: 3.13.3
- Numba/llvmlite: 0.66.0 / 0.48.0
- CUDA preflight: `available/strict_float64/kernel_executed/kernel_verified=true`
- Scope: `production_ray_bvh`
- Provider: `strict_float64_bvh_v1`

## 결과

100,000 primary Ray, cold 1회와 warm 3회를 같은 프로세스에서 측정했다.

| 장면 | Full p50 | Compact p50 | Workspace | 감소율 | 정합성 |
| --- | ---: | ---: | ---: | ---: | --- |
| stochastic depth 2 | 0.06214초 | 0.06409초 | 31.59→17.02 MB | 46.11% | PASS |
| trapped depth 10 | 0.09581초 | 0.09598초 | 77.73→34.03 MB | 56.22% | PASS |

- full geometry capacity: 65,536
- compact geometry capacity: 512
- resident fallback: 0
- 이산 결과: exact
- 최대 absolute error: depth 2 `2.78e-17`, depth 10 `1.16e-10`
- strict `1e-9` tolerance: PASS

## 판정

PERF-4D 1차 구현은 통과했다. 다만 100k synthetic wall time은 개선되지 않았으므로
속도 가속 완료라고 해석하지 않는다. 대규모 Ray에서 VRAM 증가를 억제하고 full
event geometry 다운로드를 피하는 기반으로 승인한다.

## 1M 확장 확인

같은 RTX 3070에서 1,000,000 primary Ray로 추가 측정했다.

| 장면 | Full p50 | Compact p50 | 속도비 | 정합성 |
| --- | ---: | ---: | ---: | --- |
| stochastic depth 2 | 0.47421초 | 0.44930초 | 1.055x | exact |
| trapped depth 10 | 0.58716초 | 0.52737초 | 1.113x | strict PASS |

1M에서는 compact 경로가 5.5~11.3% 빨랐다. 다만 synthetic 고정 chunk 결과이며
실제 TV CAD의 triangle 수·열·VRAM 장시간 안정성을 대체하지 않는다.

원본: `outputs/perf4d_compact_workspace/benchmark.json`
확장 원본: `outputs/perf4d_compact_workspace/benchmark_1m.json`
