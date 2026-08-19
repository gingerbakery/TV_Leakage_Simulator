# PERF-3C Strict-FP64 CUDA Wavefront

## 요약

PERF-3C는 기존 PERF-3B의 batch/SoA/event-tape 경계에 명시적
`gpu_cuda` 계산 backend를 연결했다. GPU 프로젝트는 65,536-ray primary
chunk, strict-float64 CUDA BVH intersection, `counter_rng_v2` Numba planner,
vectorized counter apply와 compiled summary reducer를 하나의 기본 stack으로
사용한다. 8,192개보다 작은 logical intersection wave는 CUDA launch/transfer
비용을 피하기 위해 Numba CPU BVH로 보낸다.

기존 `compute_backend="cpu"`가 계속 기본값이다. 이 경로는 GPU module을
import하더라도 Numba/CUDA runtime을 import하거나 장치를 probe하지 않는다.
따라서 GPU가 없거나 optional acceleration package가 없는 PC의 기존 CPU 실행을
느리게 만들지 않는다.

이번 구현의 실제 회사 ROI 1,000,000-primary source-freeze isolated warm 3-run
p50은 `7.277951초`, p95는 `8.004967초`, 처리량은
`137,401 primary ray/s`다. 이는 이
workload의 측정값이지
LightTools와의 비교 결과는 아니다. LightTools 이상의 속도라는 주장은 동일
CAD·광학 조건의 독립 비교가 생기기 전까지 보류한다.

## 사용자/프로젝트 계약

`RayTraceConfig.compute_backend` 허용값은 다음 두 가지다.

- `cpu`: 기존 scalar/Python 기본 정책을 보존한다.
- `gpu_cuda`: PERF-3C GPU stack을 요청한다.

GPU 프로젝트의 runtime `auto` 선택은 다음과 같이 해석한다.

| 항목 | 선택 |
| --- | --- |
| Intersection dispatch | `batch` |
| Primary chunk | `65,536` |
| Requested intersection provider | `gpu_cuda` |
| Small-wave policy | active row `< 8,192`이면 `numba_cpu` |
| State/pipeline | `stable_active_soa_v1` / `soa_event_tape` |
| Reflection RNG | `counter_rng_v2` |
| Planner | `numba_cpu` |
| Counter apply | `numpy_vectorized_v1` |
| Summary reducer | `numba_cpu` |

GPU와 small-wave CPU가 모두 정상 commit된 실제 run의 effective
`intersection_provider`는 `mixed`다. Requested provider는 `gpu_cuda`이고
`gpu_cuda_used=true`이므로, 이 `mixed`는 hard fallback이 아니라 의도된 hybrid
실행을 뜻한다.

개발/benchmark에서 구체적인 runtime 인자를 넘기면 프로젝트 기본보다 그 인자가
우선한다. 예를 들어 CPU config에서 `intersection_provider="gpu_cuda"`를 직접
지정하면 hybrid 없이 GPU provider 자체 계약을 검증할 수 있다.

Frontend `.bitsam` 저장/복원에 `compute_backend`를 포함했다. 이 필드가 없던
legacy 프로젝트는 안전하게 `cpu`로 복원한다.

## CUDA provider와 정밀도

Provider contract는 `strict_float64_bvh_v1`이다.

- CUDA kernel은 `float64`, `fastmath=False`다.
- primitive index는 최종 `mesh.faces` index를 그대로 유지한다.
- `traceable_face_mask`, `ignore_faces`, `min_t`, `max_t`를 traversal 안에서
  적용한다.
- 같은 거리의 tie-break는 가장 작은 face index다.
- host scene과 결과는 caller-owned, C-contiguous, read-only이며 input과 alias하지
  않는다.
- prepared host/device scene과 thread-local device workspace를 재사용한다.
- mesh acceleration invalidation 뒤에는 CUDA scene도 다시 만든다.

CPU/GPU 하드웨어의 FMA/ULP 차이를 허용하기 위해 correctness gate는 다음처럼
분리한다.

- face index, hit/count, grid, contribution summary: exact
- distance, point/normal을 포함한 stored path: absolute/relative `1e-12`

Execution metadata의 provider contract, strict-float64 flag, device id/name,
compute capability, Numba/toolkit 정보, 배열 ownership과 모든 timing의
finite/non-negative 조건도 consumer에서 검증한다. 결과 검증이 끝나기 전에는
public 결과를 publish하지 않는다.

## Capability, fallback과 circuit breaker

GPU 부재와 provider hard failure는 서로 다른 상태다.

- GPU/driver/toolkit/Numba가 없으면 `GpuCudaUnavailable`이다. 같은 logical
  batch를 CPU BVH로 정상 실행하며 hard fallback count는 증가시키지 않는다.
- `input_prepare`, `initialize`, `execute`, `result_validation` hard failure는 해당
  logical batch의 GPU 결과를 전부 버리고 CPU로 정확히 한 번 replay한다.
- hard failure 뒤에는 run-local circuit breaker가 열려 같은 run의 이후 GPU
  시도를 막는다.
- breaker와 workspace는 concurrent run 사이에서 공유 결과 상태를 만들지 않는다.
- logical count에는 실패한 attempt와 CPU replay를 이중으로 더하지 않는다.

GPU 프로젝트의 `<8,192` small wave는 failure가 아니라
`hybrid_numba_cpu_small_wave_v1` 정상 선택이다. Numba CPU가 실패하면 그 wave는
GPU를 시도하고, 이후 GPU hard failure에는 위 atomic replay/circuit 계약을
적용한다.

## `counter_rng_v2`

`counter_rng_v2`는 `(emitter seed, primary index, depth, semantic lane)`으로
random draw를 정한다. 따라서 chunk 크기, row compaction/reorder, Python/Numba
planner와 반복 실행이 달라도 같은 semantic result를 만든다. 기존
`per_primary_seeded_v1`과는 random stream 자체가 다르므로 bit-exact 비교 대상이
아니며 여러 seed의 통계 gate를 사용한다.

8 seed × 512-ray stochastic 회귀 결과는 다음과 같다.

| Stream | Gaussian / total | Gaussian fraction | Emitted flux 합계 |
| --- | ---: | ---: | ---: |
| `per_primary_seeded_v1` | `685 / 1,549` | `0.4422208` | `7.7450 lm` |
| `counter_rng_v2` | `749 / 1,632` | `0.4589461` | `8.1600 lm` |

Gaussian fraction absolute delta는 `0.0167253`로 gate `<=0.05`, emitted flux
relative delta는 `5.3583%`로 gate `<=10%`를 통과했다. 이는 소표본 회귀 gate이며
두 stream이 같은 개별 ray를 내야 한다는 의미가 아니다.

`counter_rng_v2` 자체는 8,192/65,536 chunk, object/SoA pipeline,
Python/Numba planner, summary/detailed, paths on/off와 path quota 조합에서 exact로
고정했다. Native planner failure도 같은 counter batch 전체를 Python reference로
한 번 replay한다.

## Batch 크기, memory와 Stop

65,536은 실제 GPU micro에서 launch/transfer amortization이 가장 좋았기 때문에
GPU 프로젝트 기본으로 선택했다. 다만 이전 CPU 기본 1,024보다 buffer capacity와
Stop 원자 단위가 크다.

- Host active/event buffer와 CUDA ray/stack workspace는 대체로 chunk와 active
  event row에 선형으로 증가한다.
- CUDA workspace는 다음 2의 거듭제곱 capacity로 유지되고 재사용되므로 run 중
  작은 wave가 와도 즉시 축소되지 않는다.
- Stop은 시작한 primary chunk와 현재 intersection chunk를 중간 publish하지
  않고 끝낸 뒤 다음 경계에서 반영한다. 따라서 65,536은 throughput을 높이는 대신
  최악 Stop 응답을 더 거칠게 만든다.
- 마지막 depth의 작은 wave를 CPU로 보내는 `<8,192` hybrid는 불필요한 CUDA
  launch와 작은 transfer를 줄인다.

1M p50 representative run에서 sampled process RSS delta는 `57,720,832 bytes`, tape-owned
peak는 `40,527,016 bytes`, run copy accounting은 `293,678,488 bytes`였다.
Tape peak/copy는 각 계약의 배열 회계이며 GPU VRAM peak나 process의 모든 객체
graph 크기가 아니다. CUDA VRAM peak는 아직 별도 계측하지 않았으므로 위 값으로
대체해 주장하지 않는다.

## 측정 결과

환경은 Windows 10, Python 3.13.3, Numba 0.66.0, CUDA 13.1 compatibility
resolver, NVIDIA GeForce RTX 3070 8 GiB, compute capability 8.6이다.

### Actual CAD intersection micro

`tv_leakage_roi_right_bottom_no_gap.stp`, 50,944 triangles, frozen 100,000 rays,
3회 warm 중앙값이다.

| Provider/chunk | p50 | 처리량 | Numba CPU 대비 |
| --- | ---: | ---: | ---: |
| Numba CPU / 8,192 | `0.089381초` | `1.119M ray/s` | `1.00x` |
| CUDA / 8,192 | `0.033730초` | `2.965M ray/s` | `2.650x` |
| CUDA / 16,384 | `0.021830초` | `4.581M ray/s` | `4.094x` |
| CUDA / 65,536 | `0.012916초` | `7.743M ray/s` | `6.920x` |

모든 CUDA case의 face mismatch와 distance tolerance mismatch는 `0`, 최대 absolute
distance error는 `2.8422e-14`였다. 최초 8,192-ray cold wall은
`0.942792초`이고 그중 lazy JIT가 `0.852086초`였다. 따라서 짧은 단발 실행은
warm 처리량만으로 판단하면 안 된다.

### Integrated synthetic negative control

모든 ray가 depth 10까지 살아 있는 100,000-primary synthetic에서 Numba CPU와
GPU의 p50은 각각 `1.787338 / 1.817925초`, GPU speedup은 `0.983175x`였다.
1,100,000 intersection row의 count/grid/summary는 exact, path tolerance mismatch와
fallback은 `0`이었다. 이 결과는 교차 micro가 빨라도 geometry가 단순하거나
plan/commit 비중이 크면 전체 GPU 이득이 자동으로 생기지 않음을 보여 준다.

### Actual ROI 1,000,000 primary

Source-freeze actual ROI run 조건은 45,167 active triangles, depth 10, summary,
stored-path quota 500, GPU auto stack이다.

- Isolated warm wall raw 3-run: `7.277951 / 7.270346 / 8.085747초`
- Wall p50/p95: `7.277951 / 8.004967초`
- P50 throughput: `137,401 primary ray/s`
- Receiver/surface/terminated: `126,609 / 2,250,471 / 873,391`
- Flux: `0.03998454755283727 lm`
- Stored paths: `500`
- Logical intersection: `176` batch / `3,085,763` ray
- CUDA attempt/success: `92 / 92` batch, `2,710,197 / 2,710,197` ray
- Hybrid Numba CPU attempt/success: `84 / 84` batch,
  `375,566 / 375,566` ray, failure `0`
- Hard intersection fallback: `0`
- Requested/effective intersection provider: `gpu_cuda / mixed`
- Counter planner attempt/success: `176 / 176`, fallback `0`, measured JIT `0`
- Reducer attempt/success: `16 / 16`, fallback `0`, measured JIT `0`
- Representative intersection/GPU kernel/hybrid CPU:
  `1.578990 / 0.822200 / 0.507024초`
- Representative plan/commit/wavefront total:
  `2.078457 / 1.387784 / 6.988246초`

이 값은 이전 단계의 1M 선형 환산이 아니라 실제 1M 3-run이다. 세 run의 outcome은
exact했다. 단일 PC와 단일 workload 수치이며 다른 GPU, CAD 크기, optical model과
thermal/power 상태의 성능을 보장하지 않는다.

별도 actual ROI 100k CPU/GPU 비교에서 count/grid/contribution과 stored-path
structure/order는 exact, stored-path 최대 absolute float delta는
`3.5527e-15`로 `1e-12` gate 안이었다.

## 테스트와 packaging

PERF-3C focused backend test는 Python `29 passed, 74 subtests passed`, packaging
test는 `4 passed`, frontend project test는 `9 passed`다. 다음 경계를 포함한다.
최종 전체 repository test는 Python `226 passed, 256 subtests passed`, frontend
`20 files / 128 tests passed`다.

- CPU default lazy import/no-probe/no-regression
- GPU config stack과 frontend/legacy `.bitsam` round-trip
- 8,192 GPU 경계와 `<8,192` CPU hybrid
- unavailable 정상 선택과 네 hard-failure phase whole-batch replay/circuit
- concurrent run breaker isolation
- result metadata/ownership/read-only/no-alias validation
- host/device scene cache와 invalidation
- face exact, distance/path `1e-12`, traceable/ignore/min/max 처리
- counter known vector, reorder/chunk/provider exact와 legacy statistical parity
- strict JSON (`allow_nan=False`)

GPU edition은 `requirements-gpu-cuda.txt`에 Numba 0.66.0/llvmlite 0.48.0을
별도로 pin한다. 기존 Lightweight package에는 자동으로 포함하지 않는다.
`build_gpu_cuda_desktop.bat/.ps1`가 opt-in 배포본을 만들고
`scripts/verify_gpu_cuda_runtime.py`가 import-only/device-kernel smoke를 분리한다.
GPU 사용 PC는 compatible NVIDIA driver와 CUDA toolkit도 필요하다. CUDA 13
Windows의 `bin/x64`, `nvvm/bin/x64`, libdevice layout은 GPU를 명시적으로 probe할
때만 좁은 compatibility resolver를 적용한다. 배포 전에는 clean PC에서 CPU-only
package, GPU edition, GPU 없음, driver/toolkit 없음, CUDA 13 환경을 각각
검증해야 한다.

검증/배포 matrix는 다음 상태다.

| 환경/상태 | 기대 결과 | 현재 증거 |
| --- | --- | --- |
| CPU project, acceleration 미설치 | CUDA/Numba import·probe 0, 기존 CPU 결과 | subprocess/unit 통과 |
| GPU project, GPU/driver/toolkit/Numba 없음 | CPU 정상 선택, hard fallback 0, unavailable reason | injected capability/unit 통과; clean PC pending |
| GPU input/init/execute/result corruption | whole-batch CPU replay 1회, run-local circuit | phase별 unit 통과 |
| GPU project, wave `<8,192` | probe/launch 없이 Numba CPU | 37-ray unit 통과 |
| GPU project, wave `8,192` | CUDA 경계 | 8,192-ray unit/actual CUDA 통과 |
| RTX 3070 + CUDA 13.1 | strict FP64, device scene/workspace reuse | actual cold/warm benchmark 통과 |
| Concurrent 성공/실패 run | breaker와 count 격리 | threaded unit 통과 |
| Legacy `.bitsam` | `compute_backend="cpu"` 복원 | frontend test 통과 |
| Lightweight/GPU edition packaging layout | optional dependency와 DLL 포함 분리 | packaging unit 4 passed |
| Lightweight/GPU edition ZIP 재추출 | device/kernel, CPU fallback | 동일 PC 전체 build 통과; 별도 clean PC pending |
| VRAM peak/Stop latency | 65,536 정책의 실제 upper bound 기록 | pending |

## Artifact와 해석 제한

Committed benchmark는 `scripts/benchmark_perf3c_gpu_cuda.py`다. Actual CAD micro와
synthetic control은 git-ignored `outputs/perf3c_gpu_cuda/summary.json`, actual ROI
1M 3-run은 `outputs/perf3c_gpu_cuda/actual_roi_1m.json`에 strict JSON으로 기록한다.

- Micro/control summary SHA256:
  `e2ced20f63ea8a654b3db4cae65d32b35790e6aa73b4c96b917cc293e8ffb527`
- Actual ROI 1M SHA256:
  `13ca76ce6c4e8129ae7b5dfefbadaca8c20d06884b7264d0c60a5e65812fef2e`
- Benchmark SHA256: `a9eb801d1931ba3cb5ff9549d3631a001e56f7884bdaf6291c17a390dac952f5`
- 두 artifact 모두 source 시작/종료 hash가 같고 stable이다.
- Actual `.bitsam`/CAD 원문은 repository에 넣지 않고 input hash와 workload
  contract만 artifact에 기록한다.

현재 완료 범위는 strict-float64 CUDA BVH intersection과 이를 사용하는 GPU
wavefront stack이다. CUDA에서 reflection planning/reducer 전체를 수행하는
end-to-end device-resident renderer는 아니다. 다음 성능 작업은 profiler 근거에
따라 host plan/commit과 transfer를 더 줄이고, VRAM peak 및 Stop latency를 실제
측정하며, 여러 GPU/CPU-only clean PC에서 배포 gate를 닫는 것이다.
