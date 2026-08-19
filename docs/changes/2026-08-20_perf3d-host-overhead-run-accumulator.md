# PERF-3D Host Overhead 제거와 Run Accumulator

## 요약

PERF-3D는 PERF-3C GPU stack의 교차 결과를 그대로 유지하면서 primary chunk마다
반복되던 Python host 작업을 줄였다.

- reflection seed를 `numpy_splitmix64_batch_v1`으로 한 번에 만든다.
- Receiver 후보를 object list 대신 `numpy_numeric_batch_v2` numeric batch로
  계산한다.
- compiled ordered reducer의 numeric accumulator를 run 안에서 유지하고 마지막에
  한 번만 public Python 결과로 복원한다.
- stored-path quota가 receiver-only 상태로 포화되면 이후 tape의 불필요한 path
  payload를 다시 만들지 않는다.

실제 ROI 1,000,000-primary, depth 10, stored paths 500의 source-frozen warm
3-run p50은 PERF-3C `7.277951초`에서 `5.541795초`로 줄었다. 같은 장면과 장치에서
`1.3133x`, latency `23.855%` 개선이며 처리량은 `137,401`에서
`180,447 primary ray/s`로 증가했다. Receiver/surface/terminated, flux, stored
path와 ordered semantic hash는 모든 실행에서 exact했다.

이는 실제 사용자 workload에서 체감할 수 있는 규모의 개선이다. 다만 단일 RTX
3070과 단일 실제 장면의 warm 결과이며 LightTools와의 동일 조건 비교가 아니다.
또한 PERF-3D는 전체 ray state와 scene을 GPU에 계속 상주시킨 구현이나 fused CUDA
depth kernel이 아니다. 여기서 retained/resident는 run-local **CPU numeric reducer
accumulator**에만 해당한다.

## 선택 정책과 CPU 안전성

`run_direct_ray_trace()`의 runtime-only `wavefront_reducer_commit`은 다음 값을
허용한다.

| 값 | 동작 |
| --- | --- |
| `auto` | GPU project는 `run_accumulator`, CPU project는 `per_tape` |
| `per_tape` | 각 sealed tape를 기존처럼 검증·복원·publish |
| `run_accumulator` | tape별 numeric 결과를 run-local accumulator에 유지하고 마지막에 1회 복원 |

기본 `compute_backend="cpu"`와 legacy `.bitsam`은 계속 `per_tape`를 사용한다.
따라서 이 최적화 때문에 Numba/CUDA를 새로 import하거나 장치를 probe하지 않는다.
Fresh-process paired gate에서도 Numba module은 `sys.modules`에 없었고 CUDA/native
cache는 전후 모두 비어 있었다.

CPU default paired p50은 actual case `2.638041 -> 2.590499초`(`-1.80%`),
synthetic case `3.178607 -> 3.204404초`(`+0.812%`)였다. 두 case 모두 사전 정의한
`3%` no-regression gate 안이며 semantic/count/path가 exact했다. 즉 GPU가 없는
PC의 기본 CPU 경로를 느리게 만드는 구조적 반대급부는 추가하지 않았다.

GPU project는 이미 PERF-3C에서 Numba reducer를 명시적으로 사용하는 stack이므로
`auto`에서 run accumulator를 선택한다. 개발/benchmark에서는 세 commit policy를
명시해 A/B할 수 있다.

PERF-3C intersection 계약도 그대로다. CUDA kernel은 strict `float64`,
`fastmath=False`이고 input/initialize/execute/result-validation failure는 부분
GPU 결과를 publish하지 않은 채 logical batch 전체를 CPU로 한 번 replay한다.
Circuit breaker는 run-local이며 concurrent run과 상태를 공유하지 않는다. 이번
canonical의 intersection hard fallback은 `0`이다.

## Run-retained ordered accumulator

기존 `per_tape`는 각 primary chunk마다 public dict/grid/path 상태에서 numeric
accumulator를 다시 만들고 native 결과를 검증한 뒤 Python 객체로 복원했다.
`run_accumulator`는 첫 tape에서 만든 accumulator를 이후 tape로 넘겨 numeric
state를 유지한다. Dict insertion order, strict `float64` 누산 순서와 stored-path
선택은 기존 ordered reducer 계약을 그대로 따른다.

공개 결과에 대한 transaction 경계는 다음과 같다.

1. 각 native tape 결과는 owned/C-contiguous/read-only/non-alias 검증을 통과한다.
2. 다음 tape에 넘길 retained state는 성공한 결과만 owned mutable clone으로 만든다.
3. Receiver grid와 summary dict는 final flush 전까지 수정하지 않는다.
4. Stored path는 tape별로 완전한 staged copy를 만든 뒤 원자적으로 publish한다.
   이 publish가 다음 tape의 quota/dead-end payload gate를 결정한다.
5. 정상 완료 또는 Stop으로 확정된 마지막 chunk 뒤에 numeric summary를 한 번만
   Python 상태로 복원하고 publish한다.

두 번째 이후 tape의 native `initialize`, `execute`, `result_validation` 또는 apply
준비가 실패하면 손상된 candidate를 버린다. 먼저 이전 성공 tape까지의 retained
state를 한 번 flush하고 실패한 logical tape 전체를 Python ordered reducer로
정확히 한 번 replay한다. 이후 run-local circuit breaker가 native 재시도를 막는다.
Attempt/replay를 logical tape/primary/event count에 이중 집계하지 않는다.

Accumulator와 breaker는 run-local이다. Concurrent run은 numeric state, dict
insertion order, fallback count를 공유하지 않는다. Stop은 기존 primary chunk
원자성을 유지하며 시작한 chunk를 끝낸 뒤 final flush 1회로 결과를 확정한다.

Failure path에서 prior retained state를 flush한 시간은
`wavefront_reducer_final_flush_sec`에는 기록되지만 현재
`wavefront_commit_sec`/replay/native-apply breakdown에 완전히 중복 반영되지는
않는다. 따라서 실패를 주입한 run의 세부 timing 합은 wall보다 작을 수 있다.
Fallback `0`인 canonical 성능과 transaction 정확성에는 영향이 없으며 향후 metric
scope 정리 대상이다.

## Seed와 Receiver host overhead

`_wavefront_reflection_seeds()`는 emitter seed와 연속 primary index를 unsigned
64-bit SplitMix64 batch로 계산한다. Scalar oracle인
`_wavefront_reflection_seed()`와 `uint64` wrap 경계, 음수와 `2**64` 초과 입력까지
bit-exact다. Counter RNG의 `(seed, primary, depth, semantic lane)` 계약과
`counter_rng_v2`의 chunk/provider/reorder exact 성질은 바뀌지 않는다.

`_find_first_receiver_hits_numeric()`는 distance, Receiver index, grid row/column,
received power와 hit point를 owned contiguous NumPy array로 반환한다. Receiver
입력 순서와 nearest-hit/tie 의미는 기존 scalar
`_find_first_receiver_hit()` oracle과 exact하다. Metrics는 다음 dispatch를
기록한다.

- `wavefront_reflection_seed_dispatch="numpy_splitmix64_batch_v1"`
- `wavefront_receiver_dispatch="numpy_numeric_batch_v2"`

## Stored-path quota와 payload suppression

Stored-path quota가 아직 비었거나 oldest dead-end 교체 후보가 남아 있으면 full
path payload가 필요하다. 저장소가 quota까지 찼고 모든 slot이 receiver path가 된
순간부터는 뒤 tape가 기존 결과를 바꿀 수 없으므로 payload를 생략한다.

이 전이는 run-local monotonic latch다. 한 번 `full -> omitted`가 되면 이후
Receiver/escaped/terminated 분포나 chunk 경계와 무관하게 full payload가 다시
나타나지 않는다. Effective metric은 한 run 안의 실제 tape에 따라
`full_path_v1`, `omitted_v1` 또는 `mixed_v1`이다. Requested mode는 별도 metric에
남는다.

다음 count를 함께 기록해 mode 문자열만으로 잘못 해석하지 않도록 했다.

- full/omitted/suppressed chunk count
- full/omitted primary count
- full/omitted surface-event count

`store_ray_paths=false` 또는 `max_stored_paths=0`은 처음부터 omitted이다. Quota가
포화되기 전 dead-end path가 있으면 replacement 가능성을 보존하기 위해 full을
유지한다.

## 실제 ROI 1,000,000-primary 결과

조건은 PERF-3C canonical과 같은 45,167 active triangle, depth 10, summary,
stored-path quota 500, GPU auto stack이다. Source hash는 측정 시작과 끝에 같았고
strict JSON parse와 계산식 audit를 통과했다.

| 항목 | PERF-3C | PERF-3D | 변화 |
| --- | ---: | ---: | ---: |
| Warm wall raw 3-run | `7.277951 / 7.270346 / 8.085747초` | `5.715687 / 5.541795 / 5.208224초` | - |
| Wall p50 | `7.277951초` | `5.541795초` | `1.3133x`, `-23.855%` |
| Wall p95 | `8.004967초` | `5.698298초` | `-28.815%` |
| P50 throughput | `137,401 ray/s` | `180,447 ray/s` | `+31.328%` |
| Receiver / surface / terminated | `126,609 / 2,250,471 / 873,391` | exact same | exact |
| Receiver flux | `0.03998454755283727` | exact same | exact |
| Stored paths | `500` | `500` | exact order/value |

세 PERF-3D run과 PERF-3C의 semantic hash는 모두
`0abf4da3a380c20fa0d40f862f532ac7820222cca165029bb271d04508eb71a3`다.

Representative run의 logical intersection은 `176 batch / 3,085,763 ray`다.
CUDA는 `92 / 2,710,197`, hybrid Numba CPU는 `84 / 375,566`이며 intersection,
planner와 reducer hard fallback은 모두 `0`이다. Requested/effective intersection
provider는 `gpu_cuda / mixed`다.

| Representative nested timing | PERF-3C | PERF-3D |
| --- | ---: | ---: |
| Receiver | `0.914440초` | `0.216118초` |
| Intersection | `1.578990초` | `1.985698초` |
| Plan | `2.078457초` | `1.576959초` |
| Commit | `1.387784초` | `1.145978초` |
| Wavefront total | `6.988246초` | `5.274202초` |

이 timing은 nested이므로 합산해서 wall을 재구성하면 안 된다. 특히 대표 실행의
intersection은 `0.406708초` 늘었는데도 Receiver/plan/commit 감소가 더 커 전체
wall은 개선됐다. 따라서 PERF-3D가 CUDA intersection kernel 자체를 개선했다는
주장은 하지 않는다.

Reducer는 `16` tape, `1,000,000` primary, `2,250,471` event를 retained했고 final
flush는 `1회 / 0.000637초`, fallback flush는 `0`이었다. Payload는 첫
`65,536-primary` tape만 full이고 뒤 `15` tape, `934,464 primary`는 omitted였다.
Tape copy accounting은 `293,678,488 -> 124,395,352 bytes`, 즉
`169,283,136 bytes`(`57.642%`) 감소했다. 이는 host tape array 회계이며 process
RSS나 VRAM peak를 뜻하지 않는다. Tape-owned peak는 `40,203,264 bytes`였다.

## 100k 분리 A/B

같은 host-overhead 변경 안에서 run accumulator 자체의 추가 효과를 분리하기 위해
counterbalanced 100k A/B를 실행했다.

| 경로 | p50 |
| --- | ---: |
| PERF-3C parent | `0.831689초` |
| PERF-3D `per_tape` | `0.772222초` |
| PERF-3D `run_accumulator` | `0.734558초` |

Retained policy는 parent 대비 `1.1322x`, 같은 PERF-3D per-tape 대비
`1.0513x`였다. Semantic/path/count는 exact했다. 이 표는 단일 장면 report-only
측정이며 unit test에 wall-time threshold를 넣지 않는다.

## 테스트와 benchmark

최종 repository Python suite는 `237 passed, 279 subtests passed`이며, PERF-3D
focused matrix는 `60 passed, 126 subtests passed`다. 성능 threshold 없이 다음
계약을 회귀로 고정했다.

- scalar/vector seed exact와 `uint64` wrap/count validation
- numeric Receiver와 scalar candidate exact, input 불변과 output ownership
- `per_tape`/`run_accumulator`의 ordered dict/float-bit/grid/path exact
- retained clone의 owned/mutable/non-alias 계약
- 두 번째 tape failure의 prior-state flush, failing-tape replay 1회와 circuit
- Stop complete-chunk final flush와 concurrent run 격리
- CPU default CUDA/Numba/native no-import/no-probe
- GPU `auto` commit policy 선택
- path quota dead-end gate, full→omitted 전이와 no-reappearance
- requested/effective payload 및 tape/primary/event count invariant
- strict JSON `allow_nan=False`

재현 benchmark는 `scripts/benchmark_perf3d_host_overhead.py`다. Contract
`perf3d_host_overhead_benchmark_v1`은 per-tape/retained 순서를 교차해 실행하고,
ordered float-bit/JSON semantic hash, provider evidence, source 전후 hash를 기록한다.
명시 provider가 available인데 measured native success가 없거나 fallback이 있으면
측정을 실패 처리한다. 성능 threshold는 report-only다.

이 tracked benchmark는 repository의 deterministic depth-10 scene으로 commit policy
A/B를 재현하는 harness다. 아래 actual 1M artifact의 사용자 ROI/CAD 전용 ignored
harness와 workload는 다르며 서로의 성능 수치를 대신하지 않는다.
Tracked benchmark script SHA256은
`6286f6a6be8d108e071bd69b7748da6a72c25c4b4cd90e2991ee44e90625928e`다.

Canonical actual artifact는 git-ignored
`outputs/perf3d_resident_wavefront/actual_roi_1m.json`, SHA256은
`a065f09ce69eee6f2729b9438e8d4792aa7601fe1fbe032c4874fa3458db46a9`다.
CPU default paired artifact는 같은 폴더의 `cpu_default_10k.json`, SHA256
`627ffb8b8facb7e1fa98b88dff0c34998fc87097b1951566131314ae97034298`다.
Artifact contract/folder의 `resident`는 run-local CPU accumulator retention을
뜻하며 GPU 전체 pipeline residency를 뜻하지 않는다.

## 배포 영향과 다음 병목

Lightweight와 GPU edition을 모두 PERF-3D source로 다시 빌드해 배포해야 한다.
기존 PERF-3C ZIP은 이번 host-overhead 변경을 포함하지 않으므로 PERF-3D 결과로
표기하면 안 된다. GPU edition은 run accumulator를 자동 사용하며 Lightweight는
기본 CPU/no-probe 정책을 유지한다.

최종 ZIP SHA-256과 byte size는 package 밖의 `<zip-name>.sha256` sidecar와 release
보고에 둔다. Package 내부 포함 문서에는 자기 ZIP hash를 넣지 않아 문서 변경과
ZIP hash가 서로를 다시 바꾸는 자기참조를 피한다. 이 상세 change report는 기존
build 범위를 유지해 repository-only로 둔다.

최종 freeze 결과는 다음과 같다. 이 표는 package에 복사되지 않는 repository-only
report에 있으므로 ZIP hash의 자기참조를 만들지 않는다.

| Edition | Folder bytes | ZIP bytes | ZIP SHA-256 |
| --- | ---: | ---: | --- |
| Lite | `371,538,673` | `106,483,342` | `d4c84ebcc27b63094855b18629e4ea6a6c324f0af5ddb33b57b95d4c50159a21` |
| GPU CUDA | `505,232,735` | `152,432,227` | `5f46f8610e01ad22fcdb16931b0718b47b90240f3be594b37de27aead28aa1ef` |

두 sidecar, ZIP CRC와 entry set이 exact했다. Repo↔folder↔ZIP의 source, README와
기존 포함 문서 3종 stream aggregate는 모두
`499b2e117b0e6a44baccf2e98220efe2c59fecbcf605df0c4220b0f4e4134810`로
일치했다. 두 edition 모두 repository `237` test를 통과했고 Lite의 acceleration
optional case `30`개는 expected skip이었다. Lite는 Numba/llvmlite 미포함과
CPU no-import/no-probe를, GPU edition은 RTX 3070 strict-FP64 device kernel
checksum `33153`을 재추출 package에서 확인했다.

현재 ray state, Receiver/geometry/planner, event tape와 ordered publish는 여전히
host가 제어한다. 다음 주요 단계는 active depth ray를 장치에 더 오래 유지하고
intersection과 reflection planning 사이의 host 왕복을 줄이는 fused depth CUDA
kernel이다. 그 단계에서도 strict FP64, `counter_rng_v2`, ordered 결과,
whole-logical-batch fallback과 CPU default no-probe 계약을 보존해야 한다.
