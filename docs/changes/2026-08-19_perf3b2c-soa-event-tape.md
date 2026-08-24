# PERF-3B-2C SoA Active State와 Ordered Event Tape

## 결과

PERF-3B-2B multi-bounce wavefront의 Python ray-state object graph를 대체할 수
있는 SoA active state와 actual-event 비례 compact tape를 구현했다. 계산은 기존과
같이 depth-major로 진행하지만, 완료된 chunk는 primary 순서 CSR tape로 seal한 뒤
Python ordered reducer가 Receiver grid, optical/reflection/contribution summary와
stored path를 재생한다.

이번 단계는 후속 compiled reducer와 CUDA가 공유할 데이터 경계를 고정한
experimental runtime 경로다. 실제 ROI counterbalanced 측정에서 SoA wall p50은
`6.397611초`로 object-reference `5.216227초`보다 `22.65%` 느렸다. 따라서
`wavefront_pipeline="auto"`는 `object_reference`를 유지한다. 일반 실행,
GPU·Numba가 없는 PC와 기존 CPU 결과에는 변화가 없다. SoA 경로는 benchmark와
정합성 검증에서 `wavefront_pipeline="soa_event_tape"`를 명시할 때만 사용한다.

## Runtime 선택 계약

`run_direct_ray_trace()`에 프로젝트 JSON이나 `.bitsam`에 저장하지 않는
runtime-only `wavefront_pipeline` 인자를 추가했다.

- `auto`: 검증된 `object_reference` 경로를 사용한다.
- `object_reference`: PERF-3B-2B Python ray-state/ordered commit을 명시한다.
- `soa_event_tape`: 이번 단계의 SoA state, compact tape와 Python ordered
  reducer를 명시한다.

Pipeline 선택은 기존 multi-bounce wavefront 진입 조건 안에서만 의미가 있다.
즉 explicit `intersection_dispatch="batch"`, fast virtual-plane emitter,
`max_depth >= 2` 조합에서만 사용한다. 기본 scalar dispatch, face/polygon emitter,
`max_depth <= 1`은 event tape를 만들지 않으며 Numba provider/planner를 새로
probe하지 않는다.

## SoA active state

`stable_active_soa_v1`은 한 primary chunk의 active ray를 다음 owned NumPy
배열로 유지한다.

- stable primary slot/index
- origin/direction, power
- 이전 source face와 ray-kind code
- primary별 reflection seed

다음 depth로 진행할 row는 원 active row의 strictly increasing 순서로 compact한다.
따라서 breadth-first 계산 중에도 primary 상대 순서가 바뀌지 않는다. Input과
continuation 배열은 복사해 소유하고 contiguous 배열로 유지하므로 caller mutation과
alias하지 않는다.

## Actual-event CSR tape

Tape 계약은 `ordered_primary_event_tape_v1`이다. Builder는 depth 순서의 surface
segment와 terminal을 받으며, seal할 때 primary-major CSR로 전치한다.

- `offsets`는 primary별 surface event 범위를 가리킨다.
- event 배열에는 실제로 발생한 surface hit만 저장한다. Receiver/escaped/blocked
  terminal은 primary-aligned 별도 배열에 저장한다.
- surface event에는 face, point/normal/distance, incoming/reflected/emitted power,
  reflection status/lobe와 incoming ray kind를 기록한다.
- initial ray와 terminal Receiver cell/flux/geometry를 별도 primary 배열로
  보존한다.
- sealed 배열은 owned, C-contiguous, read-only이며 dtype과 shape가 고정된다.

Seal validation은 CSR offset, finite/nonnegative 수치, 허용된 reflection status,
lobe/ray-kind, terminal 하나, depth 연속성, power의 `float64` bit chain과
terminal kind/power의 일관성을 검사한다. Unsupported planner row나 부분적으로
작성된 path는 유효 tape로 seal할 수 없다.

`wavefront_event_count`는 terminal이 아니라 실제 surface event 수다.
`wavefront_event_tape_peak_bytes`는 run 전체 합이 아니라 한 primary chunk에서
builder와 seal storage가 동시에 존재할 때의 최대 추정 bytes다.

## Python ordered reducer

Reducer 계약은 `python_ordered_v1`이다. 각 chunk에서 `primary_slot=0..N-1`
순서로 tape를 replay하고, chunk도 emitter의 기존 primary 순서대로 commit한다.
따라서 다음 순서를 object-reference와 동일하게 보존한다.

- Receiver heatmap cell과 flux의 floating-point 누적
- summary/detailed face, component, material, depth contribution의 key 생성 순서
- optical/reflection counter와 flux 누적
- Receiver 우선 stored-path quota와 가장 오래된 dead-end 교체 순서

정량 집계는 tape column을 직접 읽는다. 전체 ray-state object graph를 다시 만들지
않으며, 저장 quota에 실제로 들어갈 path만 `RayHit` 목록으로 materialize한다.
`wavefront_reducer_hydrate_sec`는 이 선택적 stored-path materialization 시간이고,
`wavefront_reducer_replay_sec`는 나머지 ordered replay 시간이다.

이번 reducer는 Python 구현이다. Native/compiled reducer, event-level 부분 fallback
또는 CUDA commit은 이번 범위가 아니다. Intersection/planner failure는 tape를
seal하기 전 기존 whole-depth fallback과 circuit breaker 계약으로 처리한다.

## RNG, Stop과 정합성

Reflection planning과 RNG 계약은 PERF-3B-2A/2B를 그대로 사용한다.

- random draw가 없는 deterministic 경로는 object-reference와 SoA pipeline의
  payload, float bit와 dict key 순서가 exact하다.
- mixed, Lambertian, Gaussian과 Russian roulette는
  `per_primary_seeded_v1`을 사용하므로 같은 wavefront seed에서 pipeline, chunk,
  반복과 intersection provider가 달라도 exact하다.
- stochastic wavefront와 legacy depth-first scalar의 관계는 계속 statistical
  parity이며 개별 ray/grid exact를 주장하지 않는다.

Stop은 primary chunk 원자성을 유지한다. 시작한 chunk의 모든 depth, tape seal과
ordered replay를 완료한 뒤 다음 chunk를 시작하지 않는다. 따라서 partial tape나
전역 집계의 절반 commit은 외부 결과로 노출되지 않는다.

## 성능 metric

기존 PERF-3B-2B metric에 다음 필드를 추가했다.

- 선택: `requested_wavefront_pipeline`, `wavefront_pipeline`
- state: `wavefront_state_layout`, `wavefront_state_init_sec`,
  `wavefront_state_advance_sec`
- tape: `wavefront_event_tape_contract`, `wavefront_event_tape_append_sec`,
  `wavefront_event_tape_seal_sec`, `wavefront_event_count`,
  `wavefront_event_tape_peak_bytes`
- reducer: `wavefront_reducer_contract`, `wavefront_reducer_replay_sec`,
  `wavefront_reducer_hydrate_sec`, `wavefront_reducer_logical_event_count`

SoA 경로의 reducer logical event count는 surface event count와 같고 retry를
중복 집계하지 않는다. Object-reference 또는 wavefront 미사용 경로의 tape
contract/count/bytes는 `not_used`/`0`이다. 모든 timing은 finite nonnegative
`float`, count/bytes는 JSON 정수이며 결과는 계속
`json.dumps(result.to_dict(), allow_nan=False)`로 직렬화할 수 있다.

## Canonical benchmark

실제 ROI에서 같은 source revision과 입력 hash를 고정하고 pipeline마다 1회
warmup한 뒤 3회씩 측정했다. 측정 순서는 `O,S,S,O,O,S`
(`O=object_reference`, `S=soa_event_tape`)로 counterbalance했으며 엄격한 교대가
아니다. 측정 중 source hash는 바뀌지 않았다.

- 원본/활성 ROI triangle: `50,944 / 45,167`
- primary ray `100,000`, `max_depth=10`, seed `42`
- contribution `summary`, stored path quota `500`
- chunk `1,024`, explicit Numba intersection, planner `auto`

| Pipeline | Wall p50 | Wall p95 | Primary ray/s p50 | 1M 선형 환산 | Object 대비 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `object_reference` | `5.216227초` | `5.224492초` | `19,170.94` | `52.16초` | `1.0000x` |
| `soa_event_tape` | `6.397611초` | `6.399492초` | `15,630.83` | `63.98초` | `0.8153x` |

SoA는 같은 조건의 object-reference보다 wall time이 `1.181384초`, `22.65%`
늘었다. 두 경로 모두 1M 선형 환산이 최종 목표와 거리가 있으며 SoA가 이를
개선하지 못했다.

P50 내부 timing은 다음과 같다. SoA state init은 state build와 같은 구간이며
별도 비용으로 더하지 않는다. State advance와 tape append/seal은 plan 안에,
reducer replay/hydrate는 commit 안에, native execute는 intersection dispatch
안에 포함되는 nested metric이다. 따라서 행을 서로 더하면 안 된다.

| 구간 | Object-reference | SoA event tape |
| --- | ---: | ---: |
| State build / init | `0.477921초` | `0.082218초` |
| State advance | `0초` | `0.021038초` |
| Plan 전체 | `2.590310초` | `4.264059초` |
| Tape append | `0초` | `0.201680초` |
| Tape seal | `0초` | `1.423436초` |
| Commit 전체 | `0.872476초` | `1.102477초` |
| Reducer replay | `0초` | `1.065727초` |
| Stored-path hydrate | `0초` | `0.022554초` |
| Intersection dispatch | `0.469579초` | `0.464707초` |
| Native kernel execute | `0.378520초` | `0.375294초` |

SoA state 초기화/advance는 object state build보다 작았고 intersection 시간도
동일 범위였다. 그러나 depth segment를 primary-major CSR로 전치하는 seal과
Python ordered replay 비용이 이 이득보다 컸다. 다음 최적화가 reducer compile과
tape sealing 비용 감소에 집중해야 하는 근거다.

SoA의 event/reducer logical count는 각각 `225,482`로 같고 retry 중복이 없다.
Primary chunk별 tape peak 최대값은 `682,614 bytes`(약 `666.6 KiB`)였다. 이는
actual-event tape 자체의 계측값이며 object graph를 포함한 Windows process RSS
우위를 뜻하지 않는다.

Provider 증거도 여섯 measured run에서 동일했다. Intersection은 모두 effective
`numba_cpu`, `native_used=true`, fallback `0`이었으므로 단순 requested label이
아니라 실제 native intersection 측정이다. Planner는 requested `auto`에서
effective `python_cpu`, native attempt `0`, fallback `0`이었다. 따라서 이번
실제 mixed ROI 수치는 compiled reflection planner speedup을 포함하지 않는다.

Semantic oracle은 두 pipeline의 모든 warmup/측정에서 exact했다.

- Receiver/surface/terminated: `12,652 / 225,482 / 87,348`
- Receiver flux: `0.040176617410112817`
- Intersection logical row: `309,119`
- Stored path: `500`
- Path materialized/skipped: `931 / 99,069`
- Stochastic primary: `77,588`
- semantic, Receiver grid, contribution, stored-path hash mismatch: `0`

실제 ROI는 mixed stochastic workload다. 이 exact 범위는 두 wavefront pipeline이
같은 `per_primary_seeded_v1` stream을 재생한 비교이며 legacy depth-first scalar와
개별 ray/grid가 exact하다는 뜻은 아니다. Legacy scalar 비교는 기존 statistical
parity 계약을 따른다.

측정 JSON은 git-ignored
`outputs/perf3b2c_soa_event_tape/actual_roi_summary.json`에
`perf3b2c_actual_roi_comparison_v1` 계약으로 기록했다. 실제 사용자 입력은
hash만 기록하며 repository fixture로 추가하지 않는다.

- Actual artifact SHA256:
  `2ec271b49466a9583220876a201510dc98f04c52be5274fa983f908c359fd5ec`
- Benchmark script SHA256:
  `1872a530e737d632df2af535c09bd44c78c17dbb1faa1e7dd2baa07dc92d9620`

Committed deterministic depth-10 synthetic matrix는 다음 명령으로 재현한다.

```powershell
python scripts/benchmark_perf3b2c_soa_event_tape.py --rays 10000 --repeats 3 --warmups 1 --batch-sizes 256 1024 4096 --provider numba_cpu --planner-provider auto --no-write
```

쓰기 모드는 같은 output directory의 `summary.json`에
`perf3b2c_soa_event_tape_v1` 계약을 기록한다. 이 synthetic 결과와 실제 ROI
`actual_roi_summary.json`은 workload와 결과 계약이 서로 다르므로 혼용하지 않는다.

## 검증

- SoA input ownership, dtype/contiguity와 stable compaction
- actual-event CSR offset, primary event order와 sealed read-only 배열
- status/lobe/ray-kind/power/terminal chain validation과 empty tape
- deterministic depth 2/10 object-reference 대비 full float-bit/dict-order exact
- summary/detailed contribution, Receiver grid/flux와 reflection summary exact
- mixed/Gaussian/Russian-roulette의 chunk/provider exact
- stored-path off와 quota `0/1/2/12`의 선택/순서 exact
- Stop primary-chunk 원자성과 기본 scalar no-probe
- strict JSON과 새 timing/count metric 타입
- 전체 Python suite `172 passed`

## 한계와 다음 단계

이번 단계는 object graph를 제거할 수 있는 state/tape 표현을 만들었지만 Python
ordered replay 비용이 남아 있어 end-to-end 가속 단계가 아니다. 백만 ray와
LightTools 이상 속도 목표 달성을 주장하지 않는다.

다음 순서는 다음과 같다.

1. `ordered_primary_event_tape_v1`을 직접 소비하는 compiled ordered reducer를
   추가한다.
2. Receiver/contribution/reflection 누적의 원 primary 순서와 `float64` bit 계약을
   유지하면서 Python per-event 호출을 줄인다.
3. `counter_rng_v2`로 stochastic draw를 row-addressable하게 만들어 compiled
   planning 범위를 넓힌다.
4. 같은 SoA/tape를 CUDA traversal/planning과 whole-depth CPU fallback 경계에
   재사용한다.

Compiled reducer가 실제 mixed ROI end-to-end, 메모리, cold start, CPU 무회귀
gate를 통과하기 전까지 `auto=object_reference`를 유지한다.
