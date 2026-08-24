# PERF-3B-2B Compiled Wavefront Planner와 Ordered Compaction

## 결과

PERF-3B-2A의 multi-bounce wavefront에서 hit point/normal 복원과 stored-path
quota 판정을 줄이고, deterministic reflection decision을 선택적으로 Numba에서
계산할 수 있는 runtime planner 경계를 추가했다.

최종 source에서 batch-size 인자를 생략해 runtime 기본 1,024를 사용한 실제 ROI
canonical warm wall 중앙값은 `5.2553초`였다. PERF-3B-2A의 같은 1,024 조건
`7.0649초` 대비 `1.344x`, legacy Python scalar `26.193초` 대비 `4.984x`,
역사적 Numba intersection scalar 약 `11.42초` 대비 약 `2.173x`다.
현재 2B wavefront의 반복 실행과 `auto`/`python_cpu` planner 설정 사이에서 모든
정량 결과와 stored path가 exact 일치했다. Mixed stochastic 장면의 legacy
depth-first scalar와 wavefront는 서로 다른 난수 stream을 사용하므로, 위
legacy/Numba scalar 값은 timing reference이며 이 exact oracle의 범위가 아니다.

Canonical은 `wavefront_planner="auto"`라 native attempt가 `0`이었다. 복원된
surface profile도 전부 `mixed`이므로 explicit native를 요청해도 deterministic
kernel이 아니라 Python sidecar 대상이다. 따라서 실제 ROI의 이번 속도 향상은
compiled planner가 아니라 batch surface geometry materialization과 O(1)
stored-path quota에서 발생했다. `5.2553초`를 1,000,000 ray로 단순 선형
환산하면 약 `52.6초`이며, 최종 속도 목표를 달성했다고 주장하지 않는다.

## 구현 범위

### Batch surface geometry materialization

`RayHitBatch.materialize_surface_geometry()`는 batch의 hit point와 방향 정리된
normal을 row 순서 그대로 NumPy 배열로 만든다.

- point는 기존 scalar와 같은 `origin + direction * t` 계산 순서를 사용한다.
- normal은 mesh의 face-aligned prepared normal을 gather한 뒤 incoming direction과
  마주보도록 뒤집는다.
- miss row의 point/normal은 `0`이며 hit 여부의 source of truth는 계속
  `RayHitBatch.face_indices`다.
- scalar `HitRecord`와 tuple을 surface hit마다 만들지 않는다.
- prepared normal은 read-only이며 vertex/face 추가로 acceleration cache가
  무효화될 때 함께 다시 만든다.

이 경계는 reflection planner가 Python인지 Numba인지와 독립적이다. 실제 mixed
ROI에서도 적용되므로 이번 canonical 개선의 주된 원인이다.

### O(1) stored-path quota

`_WavefrontStoredPathQuota`는 저장된 dead-end path index를 오래된 순서의 queue로
유지한다. Quota가 찬 뒤 Receiver path가 들어오면 기존
`_store_completed_path()`와 동일하게 가장 오래된 dead-end를 교체하지만,
후보마다 최대 `max_stored_paths`개를 다시 스캔하지 않는다.

- quota `0`은 어떤 path도 materialize하지 않는다.
- quota가 남아 있으면 primary 순서대로 저장한다.
- quota가 찼을 때 dead-end는 추가 저장하지 않는다.
- Receiver path만 가장 오래된 dead-end를 교체한다.
- ordered commit과 저장 배열의 key/order를 바꾸지 않는다.

정량 Receiver grid, flux, contribution과 reflection summary는 path 시각화
저장 여부와 계속 독립적이다.

### Optional compiled reflection planner

`run_direct_ray_trace()`에 프로젝트 JSON이나 `.bitsam`에 저장하지 않는 runtime
전용 `wavefront_planner` 인자를 추가했다.

- `auto`: Python planner를 유지하며 Numba를 import/probe하지 않는다.
- `python_cpu`: 기존 Python reflection decision을 명시적으로 사용한다.
- `numba_cpu`: 지원되는 deterministic row만 strict-float64 Numba planner에
  전달한다.

Native planner 계약은 `deterministic_reflection_v1`이다. `float64`,
`fastmath=false`를 사용하고, random draw가 필요 없는 다음 조합만 지원한다.

- termination: `threshold`
- scatter: `none`, `specular`

Lambertian, Gaussian, mixed와 Russian roulette row는 Python sidecar에서 기존
`per_primary_seeded_v1` RNG 계약으로 처리한다. Native와 sidecar 결과는 원 row
위치로 복원한 뒤 기존 ordered commit으로 전달한다. Planner input/result 배열은
read-only copy이며 호출자 배열과 alias하지 않는다.

Face profile table은 explicit `numba_cpu` planner가 실제 multi-bounce surface
hit을 처음 만났을 때 한 번만 만든다. 기본 `auto`, scalar dispatch,
`max_depth <= 1`, face/polygon legacy 경로는 이 table을 만들거나 Numba를
probe하지 않는다.

이번 단계는 reflection decision만 선택적으로 compile한다. Receiver grid,
contribution, reflection summary와 path의 ordered reducer는 아직 Python commit을
사용한다.

## Planner fallback과 count 계약

Native planner가 unavailable인 것은 정상적인 capability 결과이며 hard failure로
세지 않는다. Explicit native planner의 hard failure phase는 다음과 같다.

- `input_prepare`: face table 또는 depth input 준비 실패
- `initialize`: Numba import/JIT/kernel 초기화 실패
- `execute`: kernel 실행 실패
- `result_validation`: malformed, shape mismatch 또는 unsupported native 결과

Hard failure가 발생하면 해당 depth의 deterministic native candidate 전체를
Python에서 다시 계산하고 run-local circuit breaker를 연다. 같은 depth의
stochastic sidecar와 결합한 logical reflection plan 전체는 Python 의미를
보존하며, 이후 depth에서 native provider를 반복 호출하지 않는다.

Count는 다음 의미를 가진다.

- `wavefront_planner_logical_row_count`: reflection planning 대상인 전체 surface
  hit row 수
- `wavefront_planner_python_sidecar_row_count`: 원래 unsupported row와 fallback
  replay를 포함해 Python이 실제 계획한 row 수
- `wavefront_planner_native_attempt_row_count`: native 호출을 시도한 deterministic
  candidate 수
- `wavefront_planner_native_success_row_count`: native 결과를 commit에 사용한 row 수
- `wavefront_planner_fallback_row_count`: hard failure가 난 native candidate 수

따라서 fallback row count에는 같은 depth의 stochastic sidecar를 과대계상하지
않으며, logical row count에는 native retry를 중복 반영하지 않는다. Input 준비가
provider 호출 전에 실패하면 native attempt count는 `0`이다. Depth input의
candidate를 식별한 뒤 실패하면 fallback row count는 그 candidate 수이고, face
table 준비처럼 candidate를 식별하기 전 실패하면 `0`이다.

## 성능 metric

PERF-3B-2A metric에 다음 dispatch와 timing/counter를 추가했다.

- geometry: `wavefront_surface_geometry_dispatch`,
  `wavefront_geometry_sec`, `wavefront_geometry_ray_count`,
  `wavefront_geometry_hit_count`
- path quota: `wavefront_path_quota_dispatch`,
  `wavefront_path_materialized_count`,
  `wavefront_path_materialization_skipped_count`
- planner 선택: `requested_wavefront_planner`, `wavefront_planner`,
  `wavefront_planner_contract`
- planner row: `wavefront_planner_logical_row_count`,
  `wavefront_planner_python_sidecar_row_count`, native attempt/success/fallback
  count와 row count
- planner capability/fallback: native available/used/version/disabled,
  fallback phase/reason, unavailable reason
- planner timing: face-table prepare, depth input prepare, dispatch, native execute,
  JIT compile 시간

결과는 계속 `json.dumps(result.to_dict(), allow_nan=False)`로 strict JSON
직렬화할 수 있다.

## 실제 ROI canonical benchmark

측정 조건:

- 원본 ROI mesh: `50,944` triangle
- 활성 ROI: `45,167` triangle
- primary ray: `100,000`
- `max_depth=10`, seed `42`
- contribution: `summary`
- stored path quota: `500`
- chunk: runtime 기본 `1,024` (batch-size 인자 생략)
- intersection provider: explicit `numba_cpu`
- wavefront planner: 기본 `auto`
- warm 반복 wall-time 중앙값

| 비교 경로 | Wall 중앙값 | PERF-3B-2B 대비 |
| --- | ---: | ---: |
| Legacy Python scalar | `26.193초` | `4.984x` |
| 역사적 Numba intersection scalar | 약 `11.42초` | 약 `2.173x` |
| PERF-3B-2A parent 동일 1,024 조건 | `7.0649초` | `1.344x` |
| PERF-3B-2B final default 1,024 | `5.2553초` | `1.000x` |

최종 2B canonical과 parent 모두 1,024 chunk이므로 `7.0649초`를 직접 비교
분모로 사용했다. 같은 final source의 별도 5회 chunk sweep은 1,024
`5.1601초`, 4,096 `5.1863초`였고, final-default 3회 p50 `5.2553초`와의
차이는 run-to-run 변동 범위였다.

Semantic oracle:

- Receiver hit: `12,652`
- Surface hit: `225,482`
- Terminated: `87,348`
- Receiver flux: `0.040176617410112817`
- Stored path: `500`
- Path materialized: `931`
- Path materialization skipped: `99,069`
- CAD intersection logical query row: `309,119`
- Semantic mismatch: `0`

Mismatch `0`은 현재 2B wavefront의 warmup·반복 실행과 `auto`/`python_cpu`
planner 사이의 ordered payload 비교다. Legacy/Numba scalar와의 stochastic
정합성은 기존 statistical parity 계약을 따른다.

`requested_wavefront_planner="auto"`라 native planner attempt/success는 모두
`0`이고 Python planner가 reflection decision을 처리했다. 또한 이 workload는
전부 mixed scatter라 explicit native에서도 Python sidecar 대상이다. 이 표를
native planner speedup 근거로 사용하지 않는다.

## Chunk 1,024와 4,096의 tradeoff

PERF-3B-2A에서는 1,024가 더 빨랐다. 2B stable-source compact paths-on 실제
ROI를 1,024와 4,096로 다섯 번씩 교대 재측정한 p50은 각각
`5.1601/5.1863초`였다. 차이는 약 `0.51%`로 측정 잡음 범위이며 1,024가
근소하게 빨랐다. 처리량 이점이 없는 4,096 대신 현재 runtime 기본은 memory와
Stop 단위가 작은 1,024다.

| Chunk | Synthetic depth-10 `tracemalloc` peak | Stop 원자 단위 | Paths-on 처리량 |
| ---: | ---: | ---: | ---: |
| `1,024` | 약 `9.65 MiB` | 기준 | p50 `5.1601초` |
| `4,096` | 약 `37.64 MiB` | 4배 큰 primary chunk | p50 `5.1863초` |

Chunk는 프로젝트 파일에 저장하지 않으며 semantic 결과에는 영향을 주지 않는다.
위 메모리는 별도 synthetic 진단의 Python allocation peak이며 실제 ROI의
Windows process RSS가 아니다. Chunk 증가에 따른 약 4배 scratch scaling을
보여주는 참고값이다.

## Deterministic planner synthetic benchmark

실제 mixed 모델과 별도로 compiled planner의 경계 비용을 확인하기 위해
deterministic specular depth-10 synthetic scene을 사용했다. 각 scenario는
10,000 primary ray, chunk 4,096, warm p50이며 intersection provider는 동일한
Numba CPU다.

| Scenario | Python planner | Numba planner | Speedup |
| --- | ---: | ---: | ---: |
| Summary, paths off | `1.304850초` | `1.113563초` | `1.172x` |
| Summary, paths on | `1.476219초` | `1.189662초` | `1.241x` |
| Detailed, paths off | `1.316303초` | `1.205550초` | `1.092x` |
| Detailed, paths on | `1.390839초` | `1.290724초` | `1.078x` |

네 scenario 모두 scalar semantic reference, Python planner, Numba planner 사이의
mismatch가 `0`이었다. Compiled planner가 지원 범위에서 이득을 보였지만 실제
mixed ROI에서는 사용되지 않았고, 범용 workload와 배포/JIT gate가 남아 있어
기본 `auto` 승격은 보류한다.

재현 benchmark:

```powershell
python scripts/benchmark_perf3b2b_wavefront_compaction.py --rays 10000 --repeats 3 --warmups 1 --batch-sizes 4096 --provider numba_cpu --planner-provider python_cpu --no-write
python scripts/benchmark_perf3b2b_wavefront_compaction.py --rays 10000 --repeats 3 --warmups 1 --batch-sizes 4096 --provider numba_cpu --planner-provider numba_cpu --no-write
```

쓰기 모드의 마지막 결과는 git-ignored
`outputs/perf3b2b_wavefront_compaction/summary.json`에
`perf3b2b_wavefront_compaction_v1` 계약으로 기록한다. 실제 ROI `.bitsam`은
성능 smoke에만 사용하며 repository fixture로 추가하지 않는다.

## 검증

- Batch surface point/normal과 scalar materialize의 row별 exact 동등성
- miss row, input 불변성, read-only normal과 mesh mutation cache invalidation
- path quota `0/1/2/12`의 기존 저장/가장 오래된 dead-end 교체 순서 보존
- depth 2/10 summary/detailed semantic payload와 dict key 순서 보존
- native/reference deterministic random/boundary row의 `uint64` bit exact,
  status/lobe exact
- stochastic sidecar의 chunk/intersection provider/repeat exact
- unavailable, `input_prepare`, `initialize`, `execute`, `result_validation`
  fallback과 circuit breaker
- input prepare failure의 native attempt `0`, candidate-only fallback row count
- 기본 `auto`와 scalar의 native provider probe/call `0`
- Stop primary-chunk 원자성과 strict JSON
- 전체 Python suite `160 passed`

## 한계와 다음 단계

실제 ROI 100,000-ray 중앙값 `5.2553초`는 이전 단계보다 개선됐지만 1,000,000
ray 선형 환산은 약 `52.6초`다. LightTools 이상의 속도 또는 최종 목표 달성을
주장하지 않는다.

다음 최적화 순서는 다음과 같다.

1. Ray state를 Python object list에서 SoA buffer로 옮긴다.
2. Depth 결과를 compact event tape로 기록한다.
3. Grid/contribution/reflection/path를 exact 원 순서로 적용하는 ordered reducer를
   compile한다.
4. `counter_rng_v2`로 stochastic draw를 row-addressable하게 만들어 compiled
   planner 범위를 넓힌다.
5. 동일 SoA/event-tape와 whole-depth fallback 계약을 CUDA backend에 재사용한다.

기본 `auto` 승격은 실제 mixed ROI end-to-end, stochastic parity, GPU·Numba가
없는 PC의 CPU 무회귀, cold start와 배포 크기를 모두 통과한 뒤 결정한다.
