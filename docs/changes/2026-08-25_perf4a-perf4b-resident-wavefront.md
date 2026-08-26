# 2026-08-25 PERF-4A / PERF-4B 구현 이력

## 변경 목적

1억 primary Ray와 최대 10회 이상 반사에서 병목을 재현하는 고정 성능 계약을 만들고,
반사 depth마다 발생하던 CPU/GPU 왕복 및 CPU reflection planning을 제거한다.

## 코드 변경

- `scripts/benchmark_perf4a_target_workloads.py`
  - 세 고정 synthetic workload, cold/warm 분리, source/scene hash, 1억 Ray 환산
  - PERF-4A wavefront를 `host_roundtrip`으로 명시 고정
- `scripts/perf4_accuracy.py`
  - 이산 값 exact와 strict float64 tolerance를 분리한 재귀 비교기
- `scripts/benchmark_perf4b_resident_wavefront.py`
  - host-roundtrip과 GPU-resident cold/warm counterbalanced 비교
  - CUDA 실행 증거, fallback, parity와 1억 Ray 환산 기록
- `src/leakage_simulator/gpu_cuda_resident_wavefront.py`
  - strict-float64 CUDA BVH/Receiver/반사/감쇄/종료 fused kernel
  - 장면·광학·Receiver device binding 및 thread-local workspace 재사용
  - 한 번의 output download와 기존 primary-major event tape seal
- `src/leakage_simulator/raytracer.py`
  - runtime-only `wavefront_residency` 선택
  - 프로덕션 GPU auto에서 resident 경로 자동 선택
  - resident 통계와 실제 CUDA 실행 증거 기록
  - typed failure 시 같은 logical chunk host-roundtrip 재실행
  - residency만 명시해도 Monte Carlo pipeline auto 계약이 유지되도록 선택 조건 분리
- `tests/test_perf4a_target_workloads.py`
  - 4A 스키마와 projection 회귀
- `tests/test_perf4b_gpu_resident_wavefront.py`
  - 입력 validation, depth-10 exact, stochastic tolerance, 강제 fallback exact 회귀

## 데이터 계약

- Resident provider: `strict_float64_resident_wavefront_v1`
- State layout: `primary_thread_resident_masked_v1`
- Monte Carlo: `cpu_gpu_deterministic_batch_v1`
- Accuracy: `discrete_exact_strict_float64_v1`
- Benchmark: `perf4a_target_workload_benchmark_v1`,
  `perf4b_resident_wavefront_benchmark_v1`

## 검증 결과

- Focused PERF-4 테스트: `9 passed, 3 subtests passed`
- GPU/근접 wavefront 회귀: `59 passed, 134 subtests passed`
- 기존 CPU/GPU host-roundtrip 100k accuracy gate: 모든 case exact/pass
- PERF-4B 100k:
  - stochastic two-bounce `1.41x`
  - trapped corridor depth 10 `1.57x`
  - resident fallback 0회
  - host/resident 공개 결과 exact
- 별도 CPU/resident stochastic 8,192 회귀:
  - 이산 차이 0
  - 최대 상대오차 약 `4.1e-16`
  - 최대 ULP 2

## 남은 항목

- 사내 대표 TV ROI 장면을 원본 CAD 없이 hash 기반 PERF-4A workload로 추가
- PERF-4C에서 Receiver grid/contribution을 GPU에서 직접 집계
- event tape 전체 다운로드를 terminal/path 표본 다운로드로 축소
- VRAM peak, Stop latency, 1억 Ray 장시간 thermal 안정성 측정
- PERF-4E importance sampling/Next Event Estimation으로 5% error 달성 Ray 수 절감
