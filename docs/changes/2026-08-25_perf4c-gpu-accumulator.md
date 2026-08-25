# 2026-08-25 PERF-4C GPU 결과 누적기

## 변경 목적

PERF-4B resident wavefront 뒤에 남아 있던 전체 event tape 다운로드와 CPU ordered
summary reducer 병목을 제거한다.

## 코드 변경

- `src/leakage_simulator/gpu_cuda_summary_accumulator.py`
  - strict float64 CUDA summary accumulator와 run-local device session 추가
  - optical/reflection/contribution/Receiver/heatmap 집계 구현
- `src/leakage_simulator/gpu_cuda_resident_wavefront.py`
  - summary accumulator 연결
  - GPU path quota 선택과 selected path compact download 구현
  - summary mode에서 full event tape 다운로드 제거
- `src/leakage_simulator/raytracer.py`
  - runtime-only `gpu_accumulator=auto|gpu|host` 선택 추가
  - 기존 결과 schema에 compact summary를 원자적으로 결합
  - accumulator/path timing, byte, fallback 증거 추가
- `scripts/benchmark_perf4c_gpu_accumulator.py`
  - PERF-4B host reducer와 PERF-4C accumulator의 고정 workload 비교 추가
- `tests/test_perf4c_gpu_summary_accumulator.py`
  - 단일/다중 chunk 정합, path quota, 강제 실패 replay 회귀 추가
- 기존 PERF-4B benchmark/test는 `gpu_accumulator="host"`로 기준선을 고정했다.

## 데이터 계약

- Provider: `strict_float64_gpu_summary_accumulator_v1`
- 이산 결과: exact
- 부동소수점 합계: abs/rel `1e-9`
- 실패 단위: primary chunk 전체 replay
- `.bitsam` schema 변경: 없음

## 측정 요약

- RTX 3070, 100,000 Ray, warm 3회 p50
- Stochastic depth 2: `0.113087 → 0.060857초`, `1.858x`
- Trapped depth 10: `0.787021 → 0.102006초`, `7.715x`
- event transfer 감소: `99.925~99.978%`
- 최대 absolute error: `5.239e-10`, 이산 차이 `0`, fallback `0`

## 검증

- 전체 Python suite: `309 passed, 445 subtests passed`
- 상세 보고서: `docs/reports/2026-08-25_perf4c-gpu-accumulator.md`

## 후속 작업

- PERF-4D: traversal/shading/workspace 추가 융합
- PERF-4E: Importance Sampling, NEE, MIS 및 convergence sample 재사용
- 회사 TV ROI에서 1억 Ray 장시간 열·VRAM·정확도 측정
