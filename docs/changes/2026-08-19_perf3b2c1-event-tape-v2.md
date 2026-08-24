# PERF-3B-2C-1 Event Tape v2 검증과 Optional Path Payload

## 요약

PERF-3B-2C의 actual-event CSR을 `ordered_primary_event_tape_v2`로 올렸다.
정량 결과에 항상 필요한 core와 저장 경로 시각화에만 필요한 geometry payload를
분리하고, public strict validation과 future compiled producer용 private trusted
validation의 권한 경계를 명시했다.

일반 runtime의 explicit `soa_event_tape` 경로는 계속 public `seal()`과
`strict_v1`을 사용한다. `trusted_structural_v1`은 검증된 내부 producer를 위한
private `_seal_trusted()`에만 존재하며 사용자 입력, 역직렬화 데이터 또는 일반
Python runtime에서 선택할 수 없다. 기본 `wavefront_pipeline="auto"`는
`object_reference`를 유지하므로 GPU·Numba가 없는 PC와 기본 scalar CPU 경로에는
새 probe나 비용이 생기지 않는다.

이 보고서의 v2 source-stable canonical rerun은 같은 날 작성한 PERF-3B-2C v1
보고서의 성능 판단을 후속 갱신한다. v1 수치는 당시 구현의 historical evidence로
남기고 현재 승격 판단에는 아래 v2 수치를 사용한다.

## `ordered_primary_event_tape_v2`

v2의 event/terminal 순서와 reducer 의미는 v1과 같다.

- 계산은 depth-major다.
- seal 결과는 primary-major CSR이다.
- surface event는 실제 hit만 저장한다.
- reducer는 primary slot 오름차순으로 replay한다.
- Receiver grid/flux, summary/detailed contribution, reflection 통계와 dict key
  삽입 순서는 `object_reference`와 동일하다.
- Stop은 시작한 primary chunk 전체를 commit하는 기존 원자성을 유지한다.
- Intersection/planner 실패는 seal 전의 기존 whole-logical-depth Python fallback과
  circuit breaker를 사용한다.

v2 변경점은 path geometry의 optional 분리다.

| 구분 | 항상 저장하는 값 | 조건부 저장하는 값 |
|---|---|---|
| Primary | initial power, reflection seed | initial origin/direction |
| Surface event | face, incoming/reflected/emitted power, status, lobe, incoming ray kind | point, normal, distance |
| Terminal | kind/depth/current power/ray kind, receiver/cell, received/incoming power | receiver point/normal/distance |

`store_ray_paths && max_stored_paths > 0`이면 `full_path_v1`, 그 외에는
`omitted_v1`이다. Omitted mode의 geometry column은 `None`이나 shape가 다른 임의
배열이 아니라 명시적인 read-only empty array로 seal된다. 정량 Receiver 결과와
contribution 계산에 필요한 power/cell 필드는 어느 mode에서도 생략하지 않는다.

경로 저장이 꺼졌거나 quota가 0이면 reducer는 geometry payload를 읽거나
`RayHit` path를 materialize하지 않는다. 경로 저장이 켜진 경우에는 v1과 같은
quota 및 가장 오래된 dead-end 교체 순서를 유지하고, 실제 저장 대상으로 선택된
primary만 materialize한다.

## Validation 권한 경계

### Public strict

`PrimaryMajorEventTapeBuilder.seal()`은 인자 없는 public API이며 항상
`strict_v1`을 실행한다. 다음을 vectorized NumPy validation으로 확인한다.

- contract, payload mode, dtype, shape, C-contiguous, `OWNDATA`, read-only와
  field 간 non-overlapping storage
- CSR offset 시작/단조성/coverage와 peak-byte coverage
- finite/nonnegative power와 distance, nonnegative face
- reflection status whitelist, emitted/lobe 일관성
- terminal kind/depth/ray-kind/receiver cell 유효성
- primary별 event 수와 terminal depth 관계
- initial/event/terminal power의 `float64` bit chain
- direct/previous-lobe/terminal ray-kind chain
- blocked의 마지막 non-emitted event와 receiver/escaped emission 순서

External/untrusted tape는 반드시 이 public strict 검증을 통과해야 한다.

### Private trusted structural

`_seal_trusted()`는 `trusted_structural_v1`이며 future compiled producer와
benchmark를 위한 private 경계다. Contract/payload mode, sealed array layout,
CSR offset과 storage coverage 같은 구조 검증은 유지하지만, producer가 이미
보장해야 하는 O(event) semantic scan은 반복하지 않는다. 일반 runtime은 이
경로를 호출하지 않는다. 정상 입력에서 strict와 trusted tape의 모든 배열과
scalar field는 byte-for-byte 동일해야 한다.

## Ownership과 byte 계측

Public builder 입력은 caller 배열과 alias하지 않도록 owned C-contiguous 배열로
복사한다. Sealed 배열은 전부 read-only이며 서로 writable storage를 공유하지
않는다. Internal producer가 `_take_ownership=True`로 넘기는 배열은 명시적인
ownership transfer 뒤 read-only가 된다.

새 performance summary는 다음을 기록한다.

- `wavefront_event_tape_validation_mode`
- `wavefront_event_tape_validation_sec`
- `wavefront_event_tape_copy_bytes`
- `wavefront_event_tape_copy_contract`
- `wavefront_event_tape_path_payload`
- `wavefront_event_tape_peak_scope`

`validation_sec`은 `seal_sec`과 `wavefront_plan_sec`에 포함되는 nested timing이다.
`copy_bytes`의 현재 계약은 `builder_owned_materialization_v1`이고, caller 입력을
builder-owned storage로 materialize하거나 sealed event column을 만드는 byte
회계를 나타낸다. Producer가 builder 호출 전에 만든 advanced-index gather copy는
포함하지 않는다. `peak_bytes`의 scope는 `tape_owned_ndarray_estimate_v2`이며
strict validator의 임시 배열은 포함하지 않는다. 두 값 모두 Python process RSS,
allocator peak, GPU memory 또는 run 전체 누적 byte를 뜻하지 않는다.

Tape를 사용하지 않는 scalar, face/polygon emitter, single-bounce와 batch
`auto=object_reference`에서는 contract/validation/copy/payload/peak scope가
`not_used`, timing과 byte/count는 `0`이다.

## 정합성 검증

추가 회귀는 wall-time threshold 없이 의미 계약만 검증한다.

- public strict와 private trusted의 정상 tape byte exact
- structurally valid/semantic-invalid tape는 private trusted structure만 통과하고
  이후 public strict validation에서 거부되는 경계
- full/omitted core column byte exact
- caller input no-alias, sealed output read-only/C-contiguous/owned, non-owned
  read-only view와 field alias rejection
- malformed contract/payload/dtype/shape/writeability/offset/finite/status/lobe/
  terminal/power/ray-kind chain의 strict rejection
- trusted structural mode의 malformed layout rejection
- paths-off, quota 0와 partial quota의 object-reference 대비 float bit와 dict order
  exact
- deterministic depth 2/10, mixed/Gaussian/Russian-roulette, chunk/provider exact
- mid-depth native intersection/planner 실패의 whole logical fallback, circuit
  breaker와 중복 없는 logical count
- Stop primary-chunk atomicity
- default scalar와 batch auto의 tape/Numba no-probe
- 새 metric 타입과 `json.dumps(..., allow_nan=False)` strict JSON

최종 전체 Python suite는 `184 passed, 154 subtests passed`다.

성능 p50/p95나 speedup threshold는 단위 테스트에 넣지 않는다. 성능 승격 판단은
고정 source/data/config의 별도 canonical benchmark artifact에서만 한다.

## Canonical benchmark와 자동 선택 결정

실제 ROI에서 source와 입력 hash를 고정하고 pipeline마다 1회 warmup한 뒤 3회씩
측정했다. 순서는 `O,S,S,O,O,S`(`O=object_reference`, `S=soa_event_tape`)인
counterbalanced 순서이며 엄격한 교대가 아니다.

- 원본/활성 ROI triangle: `50,944 / 45,167`
- primary ray `100,000`, `max_depth=10`, seed `42`
- contribution `summary`, stored path quota `500`
- chunk `1,024`, explicit Numba intersection, planner `auto`

| Pipeline | Wall p50 | Wall p95 | Primary ray/s p50 | 1M 선형 환산 | Object 대비 |
|---|---:|---:|---:|---:|---:|
| `object_reference` | `5.232795초` | `5.288968초` | `19,110.25` | `52.33초` | `1.0000x` |
| `soa_event_tape` | `5.121246초` | `5.130226초` | `19,526.50` | `51.21초` | `1.0218x` |

SoA는 p50 wall time을 `0.111549초`, `2.132%` 줄였다. 그러나 자동 승격 기준
`>= 1.05x`에는 못 미친 `1.021782x`이므로
`wavefront_pipeline="auto"`는 `object_reference`를 유지한다. 1M 값은 100k
p50의 단순 선형 환산이며 최종 1M/LightTools 성능 목표 달성을 뜻하지 않는다.

P50 내부 timing은 다음과 같다. State init은 state build와 같은 구간이고 state
advance, tape append/seal은 plan 안에 포함된다. Strict validation은 seal 안에,
reducer replay/hydrate는 commit 안에, native execute는 intersection dispatch 안에
포함되는 nested metric이므로 행을 더하면 안 된다.

| 구간 | Object-reference | SoA v2 |
|---|---:|---:|
| State build / init | `0.493646초` | `0.084956초` |
| State advance | `0초` | `0.024213초` |
| Plan 전체 | `2.588661초` | `2.960907초` |
| Tape append | `0초` | `0.209180초` |
| Tape seal | `0초` | `0.102317초` |
| Strict validation | `0초` | `0.060045초` |
| Commit 전체 | `0.859558초` | `1.094071초` |
| Reducer replay | `0초` | `1.052167초` |
| Stored-path hydrate | `0초` | `0.024338초` |
| Intersection dispatch | `0.476005초` | `0.482072초` |
| Native kernel execute | `0.382826초` | `0.388773초` |

모든 여섯 measured run의 semantic/grid/contribution/path hash와 정량 결과가
exact했다.

- Receiver/surface/terminated: `12,652 / 225,482 / 87,348`
- Receiver flux: `0.040176617410112817`
- Intersection logical row: `309,119`
- Stored path: `500`
- Path materialized/skipped: `931 / 99,069`
- Stochastic primary: `77,588`

Intersection은 모든 run에서 requested/effective `numba_cpu`,
`native_available=true`, `native_used=true`였다. Run마다 attempt/success는
`1,078 / 1,078`, native success row는 `309,119`, fallback은 `0`이다. Planner는
requested `auto`에서 effective `python_cpu`, logical/Python-sidecar row
`225,482 / 225,482`, native attempt와 fallback `0`이었다. 따라서 이 수치는
compiled reflection planner speedup을 포함하지 않는다.

100k paths-on 실행은 `full_path_v1`, strict seal을 사용했고 primary chunk별 tape
peak 최대값은 `680,048 bytes`, run copy 회계는 `29,407,112 bytes`였다. 별도
10k source-stable one-run A/B는 같은 `22,713` surface event에서 다음을 기록했다.
이는 byte 계약 확인용이며 p50 성능 비교가 아니다.

| Payload | Tape peak | Copy bytes | Stored path |
|---|---:|---:|---:|
| `full_path_v1` | `643,800` | `2,952,580` | `500` |
| `omitted_v1` | `271,080` | `1,131,996` | `0` |

두 mode 모두 각 object-reference와 semantic/grid/contribution/count가 exact했다.
이 tape-owned byte 회계만으로 Windows process RSS 우위를 주장하지 않는다.

측정 JSON은 git-ignored
`outputs/perf3b2c_soa_event_tape/actual_roi_summary.json`에
`perf3b2c_actual_roi_comparison_v1` 계약으로 기록했다. 측정 전후 source hash는
동일하며 실제 사용자 입력은 hash만 기록하고 repository fixture로 추가하지
않는다.

- Actual artifact: `40,275 bytes`, SHA256
  `ef2ad80346d7e1ea44c00fc9cd19be0cfb75c9da00362231920782c486c9ad5e`
- 10k paths-on/off artifact: `38,714 bytes`, SHA256
  `98695ac925e8cbda12ca1549730a84cebd08a8ffcc91a1f49c04d21c7773b06c`
- Benchmark script SHA256:
  `89b223a2c128f83d1cfc76c5f9dee1e9aa8aee7cf5f1fb41f2ad5859c10cb783`

단일 synthetic seal microbenchmark나 구조적 byte 감소만으로 auto 승격 또는
1M/LightTools 목표 달성을 주장하지 않는다.

## 다음 단계

1. `ordered_primary_event_tape_v2` core를 직접 소비하는 compiled ordered reducer
2. Python dict/object commit을 compact event/reduction buffer로 축소
3. stochastic 경로의 `counter_rng_v2`
4. 같은 core/payload 및 whole-depth fallback 계약을 사용하는 CUDA backend

PERF-3B-2C-1은 검증·ownership·payload 비용 경계를 정리한 단계다. 최종 GPU ray
tracing 성능 목표의 완료 단계가 아니다.
