# PERF-4A 고정 성능 계약

## 목적

PERF-4A는 GPU가 켜졌다는 표시만 확인하는 시험이 아니다. 같은 장면, 같은 Ray 수,
같은 Monte Carlo 표본으로 성능과 정확도 증거를 반복 수집하는 기준선이다. 이후
PERF-4B~4E의 가속 결과는 반드시 이 계약과 비교한다.

## 계약

- 스키마: `perf4a_target_workload_benchmark_v1`
- Monte Carlo 계약: `cpu_gpu_deterministic_batch_v1`
- Wavefront 기준선: `host_roundtrip`으로 고정한다. PERF-4B 구현이 기본 경로를
  바꾸더라도 PERF-4A 기준 수치가 조용히 바뀌어서는 안 된다.
- 측정 순서: 동일 프로세스에서 workload별 cold 1회 후 warm N회
- GPU 성공 조건:
  - production CUDA preflight 성공
  - `compute_execution_state`가 `gpu_active` 또는 `gpu_mixed`
  - CUDA 성공 batch가 1개 이상
  - hard fallback 0회
- 성능 수치는 warm `p50`, `p95`를 모두 기록하며 cold 수치와 섞지 않는다.

## 고정 Synthetic Workload

| 이름 | 목적 |
| --- | --- |
| `face_direct` | Face emitter batch와 직접 Receiver hit 검증 |
| `stochastic_two_bounce` | Mixed/Lambertian, Russian roulette 검증 |
| `trapped_corridor_depth10` | 모든 Ray가 최대 반사까지 생존하는 보수적 병목 검증 |

각 workload는 geometry, emitter, receiver, optical profile, 중요 config를 직렬화한
`scene_sha256`으로 식별한다. Ray 수가 달라지면 signature도 달라진다.

## 기록 지표

- primary Ray 수와 처리량
- triangle, emitter, receiver 수
- 최대 반사, 실제 depth별 active Ray 수
- logical intersection row와 primary당 평균 교차 횟수
- Receiver hit 수와 hit rate
- CUDA upload/kernel/download 시간
- Receiver, reflection planning, ordered commit 시간
- event tape copy/peak bytes
- 1억 Ray 선형 환산과 5/10/15/20/30분 목표 대비 필요 가속률

## 실행

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4a_target_workloads.py `
  --backend gpu_cuda --rays 100000 --repeats 3
```

빠른 CPU 스키마 점검:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_perf4a_target_workloads.py `
  --backend cpu --rays 1000 --repeats 1 --no-write
```

기본 결과는 git에서 제외되는 `outputs/perf4a_target_workloads/summary.json`에
저장한다.

## 해석 제한

- Synthetic control은 사내 TV ROI 대표 장면을 대체하지 않는다.
- 1억 Ray 시간은 선형 환산이며 Receiver Error 5% 달성을 보장하지 않는다.
- 사내 대표 장면은 원본 CAD를 저장소에 넣지 않고 scene hash와 익명화된 수치만
  같은 스키마로 기록한다.
