# CAD 교차 백엔드 데이터 계약

## 목적
- ray tracing 엔진이 CAD 교차 구현 방식과 분리되도록 한다.
- brute-force, BVH, CUDA와 향후 Embree/Open3D 백엔드가 동일한 교차 결과 형식을 반환하도록 한다.
- 성능 가속 후에도 face/component/material 연결이 달라지지 않게 한다.

## 입력 계약

### Ray
- `origin`: CAD 좌표계의 `(x, y, z)`, 단위 `mm`
- `direction`: 정규화된 방향 벡터
- `min_t`: self-intersection 방지 최소 거리
- `max_t`: receiver 또는 이전 hit까지 허용되는 최대 거리
- `ignore_face`: 바로 이전에 충돌한 face를 제외할 때 사용하는 face index

### Mesh
- `vertices`: triangle vertex 좌표
- `faces`: vertex index 3개로 구성된 triangle
- `face_index`: `mesh.faces` 배열 index와 동일
- `face_metadata`: component와 원본 CAD face 연결 정보
- `face_material`: optical property 조회에 사용하는 material id

## 출력 계약
교차 성공 시 `HitRecord`를 반환한다.

- `t`: ray origin에서 hit point까지의 거리
- `point`: CAD 좌표계 hit 위치
- `normal`: ray 진행 방향의 반대쪽을 향하도록 정리된 surface normal
- `face_index`: 원본 `mesh.faces` index
- `triangle`: 해당 `TriangleFace`

교차하지 않으면 `None`을 반환한다.

## PERF-3B batch 호출 계약

Batch는 새로운 backend 종류가 아니라 여러 ray를 전달하는 호출 방식이다.
프로젝트 파일에는 저장하지 않으며 runtime 내부에서만 사용한다. 기존
`intersect_ray()`는 그대로 유지하고 다음 API를 추가한다.

```python
hits = mesh.intersect_rays(rays, backend="bvh")
```

### `RayBatch` 입력

- `origins`: `float64`, shape `(N, 3)`, CAD 좌표계, 단위 `mm`
- `directions`: `float64`, shape `(N, 3)`, 정규화된 방향 벡터
- `min_t`: `float64`, shape `(N,)`; scalar 입력은 모든 row로 확장
- `max_t`: `float64`, shape `(N,)`; `None`은 모든 row에서 `+inf`
- `ignore_faces`: `int64`, shape `(N,)`; `-1`은 제외할 face가 없다는 뜻

각 row는 독립적인 `min_t`, `max_t`, `ignore_face`를 가진다. `min_t`에는
최소 `1e-8`을 적용한다. 유효 hit 경계는 `t > min_t`, `t <= max_t`이다.
따라서 `max_t <= min_t`인 row는 즉시 miss가 된다.

### `RayHitBatch` 출력

- `t`: `float64`, shape `(N,)`
- `face_indices`: `int64`, shape `(N,)`
- miss sentinel: `t=+inf`, `face_index=-1`

출력 row는 입력 row와 일대일로 대응하며 순서를 바꾸지 않는다. GPU에서
불필요한 전송과 Python 객체 생성을 줄이기 위해 point, normal,
`TriangleFace`는 batch 결과에 넣지 않는다. 필요한 hit만
`RayHitBatch.materialize(mesh, rays, index)`로 기존 `HitRecord` 형태로
복원한다.

전체 batch 한 번과 동일한 row를 여러 chunk로 나누어 실행한 결과는
동일해야 한다. Stop/progress를 나중에 연결할 때도 batch 경계는 결과
정합성에 영향을 주지 않아야 한다.

### PERF-3B-0 최초 CPU adapter

PERF-3B-0에서 추가한 `intersect_rays()` reference 구현은 각 row를 기존
scalar `intersect_ray()`에 위임한다. 이 경로를 사용할 때는
`native_batch=false`이며 속도 향상 구현이 아니다. 이 adapter로
scalar/batch 정합성을 고정한 뒤 PERF-3B-2에서 같은 입출력 경계 뒤에
optional native CPU provider를 추가했다.

backend와 dispatch는 구분한다.

- backend: `auto`, `brute_force`, `bvh`처럼 교차를 계산하는 구현
- dispatch: 한 ray를 호출하는 `scalar`, 여러 ray를 전달하는 `batch`
- provider: 같은 backend/dispatch 계약을 실행하는 `python_cpu`, `numba_cpu`,
  향후 `gpu_cuda` 구현

이번 단계에서는 `RayTraceConfig.intersection_backend` 허용값과 자동 선택
정책을 변경하지 않는다. `gpu_cuda`는 실제 adapter, capability probe,
whole-batch CPU fallback이 준비되는 단계에서만 공개한다.

### PERF-3B-1 runtime dispatch

`run_direct_ray_trace()`는 다음 조건에서 reference batch dispatch를 사용한다.

- NumPy fast sampling을 지원하는 datum/reference-plane emitter
- `max_depth <= 1`
- runtime `intersection_dispatch`가 명시적으로 `batch`

PERF-3B-1의 `python_cpu` reference adapter는 `native_batch=false`이고
scalar보다 느렸으므로 기본 `auto`는 scalar dispatch를 유지했다. `batch`는
정합성 테스트와 benchmark를 위한 명시적 opt-in이었다. PERF-3B-2에 native
provider가 추가된 뒤에도 end-to-end 자동 선택 gate를 통과하지 못했으므로
현재 `auto` 정책은 그대로다.

기존 sampler는 기본 65,536-ray batch를 그대로 생성한다. 같은 seed에서도
sampler batch 크기를 바꾸면 NumPy 난수 draw 배치가 달라지므로 이 값은
건드리지 않는다. 생성된 origin/direction 배열만 별도의 intersection chunk로
잘라 query에 전달한다. PERF-3B-1 당시 기본은 4,096이었고 PERF-3B-2A
depth-10 sweep에서는 1,024가 더 빨랐다. PERF-3B-2B stable-source 실제 ROI
교대 재측정에서는 1,024와 4,096의 p50 차이가 약 0.51%로 측정 잡음 범위였고
1,024가 근소하게 빨랐다. 처리량 이점이 없는 4,096 대신 memory와 Stop 단위가
작은 1,024를 runtime 기본으로 유지한다.
별도 synthetic depth-10 `tracemalloc`에서 1,024/4,096의 Python allocation
peak는 약 `9.65/37.64 MiB`였고 Stop 원자 단위도 4배 차이 난다. 이는 실제 ROI
process RSS가 아니라 scratch scaling 참고값이다. 메모리나 Stop 응답성이
우선이면 runtime에서 1,024를 명시한다.

처리 순서는 다음과 같다.

1. ray별 direct Receiver 후보와 `max_t`를 계산한다.
2. primary ray를 `intersect_rays()`로 계산한다.
3. 원 row 순서대로 hit를 복원하고 reflection RNG 결정을 만든다.
4. 실제 reflection ray만 compact해 secondary batch를 계산한다.
5. Receiver grid, optical/reflection/contribution summary와 stored path를 원
   primary row 순서대로 commit한다.

Planning 단계는 정량 결과를 변경하지 않는다. 모든 누적과 path quota
교체를 원 ray 순서로 commit하므로 `python_cpu` reference에서는 scalar와
동일 seed 결과가 exact하게 같아야 한다.

PERF-3B-1 시점에는 face/polygon emitter와 `max_depth >= 2`가 기존 scalar
dispatch를 사용했다. PERF-3B-2A에서 fast virtual-plane emitter의 다회 반사만
별도 wavefront 계약으로 확장했다. runtime dispatch와 chunk 크기는 계속
테스트/benchmark용 keyword-only 인자이며 프로젝트 JSON이나 `.bitsam`에는
저장하지 않는다.

Stop은 다음 sampling batch를 만들기 전과 intersection chunk 경계에서
확인한다. 시작한 chunk는 primary, secondary와 결과 commit까지 원자적으로
완료하고 다음 chunk를 시작하지 않는다. 기본값에서 최악의 Stop 지연은
1,024 primary ray의 남은 multi-bounce 처리 시간이다.

결과 JSON에는 NumPy 배열, scalar 또는 batch miss sentinel을 노출하지 않는다.
`json.dumps(result.to_dict(), allow_nan=False)`가 성공해야 한다.

### PERF-3B-2 optional native CPU provider

`run_direct_ray_trace()`에 프로젝트에 저장하지 않는 runtime 전용
`intersection_provider` 경계를 추가했다.

- `auto`: 현재는 기존 `python_cpu`를 그대로 사용하며 Numba를 import하거나
  capability probe하지 않는다.
- `python_cpu`: 기존 Python scalar/BVH 또는 reference batch adapter를 사용한다.
- `numba_cpu`: 명시적으로 요청한 benchmark/test에서만 strict-float64 Numba
  BVH kernel을 사용한다.

`numba_cpu`는 `bvh` backend에서만 동작한다. prepared triangle과 flat BVH를
provider용 연속 NumPy 배열로 한 번 pack하고, 배열을 read-only로 만들어
동일 geometry 실행에서 재사용한다. vertex/face가 추가되어 acceleration이
무효화되면 native scene cache도 함께 폐기한다. Numba import와 JIT는 명시적
provider를 실제 호출할 때까지 지연한다.

커널은 다음 수치 계약을 그대로 유지한다.

- `float64`, `fastmath=false`
- `t > min_t`, `t <= max_t`
- miss는 `(+inf, -1)`
- `ignore_face`와 `trace_excluded` 적용
- determinant epsilon `1e-8`, AABB parallel threshold `1e-12`
- 동거리 허용 오차 `1e-10` 안에서 작은 원본 `face_index` 선택

provider가 없다는 probe 결과는 정상적인 CPU 선택이며 failure로 기록하지
않는다. 초기화, 실행 또는 결과 검증이 실패하면 시작한 logical query 전체를
Python CPU로 다시 계산한다. 일부 native 결과와 fallback 결과를 한 query
안에서 섞지 않는다. 첫 hard failure 뒤에는 해당 run의 circuit breaker를
열어 남은 query가 provider를 반복 호출하지 않게 한다. logical batch/ray
통계는 재시도를 중복 계산하지 않는다.

결과 검증은 shape/dtype와 miss sentinel뿐 아니라 각 row의 `min_t`, `max_t`,
`ignore_face`, `trace_excluded`도 다시 확인한다. 이 계약을 위반한 native
출력은 `result_validation` 실패로 처리하고 같은 logical query 전체를 Python
CPU로 재실행한다.

이번 prototype은 lightweight desktop package에 Numba/llvmlite를 포함하지
않으며 UI, `RayTraceConfig`, `.bitsam` schema를 변경하지 않는다. 실제 ROI
end-to-end 성능과 배포 크기 기준을 통과하기 전까지 `auto`는 계속 기존
Python CPU 경로를 유지한다.

### PERF-3B-2A multi-bounce depth wavefront

다회 반사 wavefront는 다음 조건을 모두 만족할 때만 사용한다.

- runtime `intersection_dispatch="batch"`를 명시적으로 요청한다.
- emitter가 NumPy fast virtual-plane sampling을 지원한다.
- `max_depth >= 2`다.

기본 `intersection_dispatch="auto"`는 다회 반사에서도 legacy scalar를
유지한다. `intersection_provider="auto"`도 기존 `python_cpu`를 사용하고
Numba를 import하거나 probe하지 않는다. Face emitter와 polygon-auto emitter는
primary sampling과 reflection이 하나의 legacy RNG stream을 공유하므로 명시적
batch 요청에서도 scalar로 남는다. Fast emitter와 face/polygon emitter가
혼합된 run은 각각 batch와 scalar를 사용하고 effective dispatch를 `mixed`로
기록할 수 있다.

한 primary chunk의 실행 순서는 다음과 같다.

1. primary index 순서대로 active state를 만든다.
2. 같은 depth의 active ray에 대해 Receiver 후보와 row별 `max_t`를 NumPy로
   계산한다.
3. active origin, direction, energy, 이전 face를 compact `RayBatch`로 만들어
   한 번에 교차한다.
4. miss/Receiver/blocked ray를 제거하고 살아남은 ray만 다음 depth state로
   compact한다.
5. chunk의 모든 ray가 종료되면 원 primary index 순서로 Receiver grid,
   optical/reflection/contribution summary와 stored path를 commit한다.

Depth별 planning 결과를 즉시 전역 집계하지 않는 이유는 부동소수점 누적 순서와
stored-path quota 교체 순서를 안정적으로 유지하기 위해서다. Stored path는 기존
정책대로 bounded collection이며, quota가 이미 찼고 새 경로가 저장소에 들어갈 수
없는 경우 시각화용 `RayHit` 객체 materialization을 생략한다. 이 생략은 Receiver
flux, hit count, contribution 또는 reflection summary에 영향을 주지 않는다.

#### Reflection RNG 계약

Legacy scalar 다회 반사는 한 emitter RNG를 primary별 depth-first 순서로
소비한다. Depth wavefront는 depth별 breadth-first 실행이므로 stochastic draw를
같은 순서로 소비할 수 없다. PERF-3B-2A는 stochastic scatter 또는 roulette
draw 조건을 처음 만난 primary에 대해 emitter seed와 emitter 내부 primary
index에서 독립 stream을 만드는 `per_primary_seeded_v1`을 사용한다.

- `specular`/`none`과 threshold 종료처럼 reflection random draw가 전혀 없는
  경로는 legacy scalar와 Receiver grid, flux, contribution, reflection summary,
  stored path가 exact하게 같다.
- Lambertian, Gaussian, mixed scatter 또는 실제 roulette draw가 있는 경로는
  같은 wavefront seed에서 chunk 크기, 반복 실행, `python_cpu`/`numba_cpu`
  provider가 달라도 exact하게 재현된다.
- stochastic wavefront와 legacy scalar는 서로 다른 Monte Carlo stream이므로
  개별 ray와 grid가 exact 같다는 계약이 아니다. 이 비교는 여러 seed의 flux,
  hit ratio와 오차 추정치를 이용한 statistical parity 계약이다.

성능 metric의 `wavefront_reflection_rng="per_primary_seeded_v1"`과
`wavefront_rng_scalar_parity="exact_no_draw_statistical_stochastic"`이 이 경계를
명시한다.

#### Stop과 provider fallback 원자성

Stop은 primary chunk 경계에서 반영한다. 이미 시작한 chunk에서 Stop이
요청되면 active depth wavefront와 ordered commit을 모두 완료한 뒤 다음 chunk를
시작하지 않는다. 따라서 최악 Stop 지연은 한 intersection query가 아니라 해당
primary chunk의 남은 전체 bounce 처리 시간이다.

각 depth의 compact batch가 하나의 logical intersection query다. Native provider가
초기화, 실행 또는 결과 검증 중 실패하면 그 depth batch 전체를 동일한 Python CPU
query로 다시 실행한다. 이전 depth의 성공 결과는 유지할 수 있으므로 run의 effective
provider는 `mixed`가 될 수 있지만, 한 depth batch 안에서 native row와 fallback
row를 섞지 않는다. Circuit breaker와 logical query count 비중복 규칙은 기존
PERF-3B-2 계약을 그대로 적용한다.

### PERF-3B-2B surface geometry와 compiled reflection planner

PERF-3B-2B는 intersection 결과와 ordered commit 사이에 두 runtime 경계를
추가했다. 프로젝트 JSON, `.bitsam`, UI schema는 변경하지 않는다.

#### Surface geometry batch

`RayHitBatch.materialize_surface_geometry(mesh, rays)`는 row-aligned point/normal
배열과 hit count를 반환한다.

- point는 scalar와 같은 `origin + direction * t` 계산 순서를 사용한다.
- normal은 face-aligned prepared normal을 gather하고 incoming direction과
  마주보도록 뒤집는다.
- miss row의 point/normal은 `0`이며 hit mask는 계속 `face_indices >= 0`이다.
- prepared normal은 read-only이며 mesh vertex/face mutation 때 acceleration
  cache와 함께 무효화한다.

이 경계는 surface hit마다 `HitRecord`와 tuple을 만들지 않지만 기존 scalar
materialize와 point/normal/face 의미를 exact 보존한다.

#### Stored-path quota

Wavefront path quota는 오래된 dead-end index를 ordered queue로 유지한다. Quota가
남아 있으면 primary 순서대로 저장하고, quota가 찬 뒤에는 Receiver path만 가장
오래된 dead-end를 교체한다. 이는 기존 `_store_completed_path()`와 같은 의미와
저장 순서를 O(1) 판정으로 보존한다. Quantitative grid/flux/contribution과 path
materialization 여부는 계속 독립적이다.

#### Runtime planner 선택

`run_direct_ray_trace()`의 runtime-only `wavefront_planner` 허용값은 다음과 같다.

- `auto`: 기존 Python planner를 사용하고 Numba를 import/probe하지 않는다.
- `python_cpu`: Python reflection planner를 명시한다.
- `numba_cpu`: 지원되는 deterministic row만 Numba planner에 전달한다.

Native planner 계약은 `deterministic_reflection_v1`, strict `float64`,
`fastmath=false`다. `threshold` termination의 `none`/`specular`만 지원한다.
Lambertian, Gaussian, mixed와 Russian roulette는 Python sidecar를 사용하며 원
row 위치로 합친 뒤 ordered commit한다. Planner input/result는 read-only copy고
호출자 배열과 alias하지 않는다.

Face profile table은 explicit native planner가 실제 multi-bounce surface hit을
처음 만났을 때 한 번만 준비한다. 기본 `auto`, scalar dispatch,
`max_depth <= 1`, face/polygon legacy 경로에서는 table 준비와 Numba probe가
발생하지 않는다.

#### Planner fallback과 count

Unavailable capability는 정상적인 CPU 선택이다. Hard failure phase는
`input_prepare`, `initialize`, `execute`, `result_validation`이다. Malformed/shape
mismatch/unsupported native output은 `result_validation`으로 통일한다.

Hard failure가 나면 같은 depth의 deterministic native candidate 전체를 Python
planner로 다시 계산하고 run-local circuit breaker를 연다. 같은 depth의
stochastic sidecar와 합친 logical plan은 기존 Python 의미를 보존하며 이후
depth에서 native planner를 다시 호출하지 않는다.

- `wavefront_planner_logical_row_count`: 전체 surface planning row
- `wavefront_planner_python_sidecar_row_count`: unsupported row와 fallback replay를
  포함해 Python이 계획한 row
- native attempt/success row count: 실제 native candidate 시도/사용 row
- `wavefront_planner_fallback_row_count`: 실패한 deterministic native candidate
  row만 집계하며 stochastic sidecar를 포함하지 않음

Input 준비가 provider 호출 전에 실패하면 native attempt count는 `0`이다.
Depth candidate를 식별한 뒤 input 생성에 실패하면 fallback row count는 그
candidate 수이고, face table 준비처럼 candidate 식별 전 실패하면 `0`이다.
Logical row count에는 native retry를 중복 반영하지 않는다. Native와 Python
planner를 같은 run에서 사용할 수 있지만 원 row 순서와 ordered commit 순서는
바뀌지 않는다.

### PERF-3B-2C SoA state와 ordered event tape

PERF-3B-2C는 multi-bounce wavefront의 계산 순서를 바꾸지 않고 active state와
commit 입력 표현을 분리한다. Runtime-only `wavefront_pipeline` 허용값은 다음과
같다.

- `auto`: 성능 gate를 통과한 기존 `object_reference`를 사용한다.
- `object_reference`: PERF-3B-2B Python ray-state/ordered commit을 명시한다.
- `soa_event_tape`: `stable_active_soa_v1` state,
  `ordered_primary_event_tape_v2` tape와 `python_ordered_v1` reducer를 사용한다.

이 값은 UI, `RayTraceConfig`, 프로젝트 JSON과 `.bitsam`에 저장하지 않는다.
Pipeline은 explicit batch, fast virtual-plane emitter와 `max_depth >= 2`인 기존
wavefront 안에서만 선택한다. Scalar, face/polygon emitter와 single-bounce는
tape를 만들지 않는다.

#### Active state와 compaction

`stable_active_soa_v1`은 primary slot/index, origin/direction, power, source face,
ray kind와 reflection seed를 owned contiguous NumPy 배열로 보관한다. 다음 depth의
continuation은 현재 active row의 strictly increasing 순서로 compact해 primary
상대 순서를 유지한다. Caller input과 alias하지 않는다.

#### `ordered_primary_event_tape_v2`

Builder는 depth-major surface segment와 primary terminal을 수집하고 seal할 때
primary-major CSR로 전치한다. CSR event 배열은 실제 surface hit만 포함한다.
Receiver, escaped, blocked terminal은 primary-aligned 별도 배열이다. 따라서
storage는 `primary_count * max_depth`가 아니라 실제 surface event 수에 비례한다.

Core는 initial power/seed, surface face/power/status/lobe/ray-kind와 정량 terminal
필드를 항상 포함한다. Initial ray와 surface/receiver의 point/normal/distance는
저장 경로에만 필요한 optional payload다. `store_ray_paths && max_stored_paths > 0`
이면 `full_path_v1`, 아니면 `omitted_v1`이며 omitted geometry column은 shape가
고정된 read-only empty array다. Receiver grid/flux와 contribution에 필요한
power/cell은 생략하지 않는다.

Sealed 배열은 owned, C-contiguous, read-only이며 nonempty storage range가 서로
겹치지 않는다. Empty array도 view가 아니라 `OWNDATA`여야 한다. Public `seal()`은
항상 `strict_v1`이며 ownership/alias, dtype/shape/offset, finite/nonnegative power와
distance, reflection status whitelist, lobe와 ray-kind, terminal 유일성, depth
연속성, surface power의 `float64` bit chain을 vectorized 검증한다. Unsupported/partial planner 결과는
seal할 수 없다. Private `_seal_trusted()`의 `trusted_structural_v1`은 future
compiled producer/benchmark 전용이다. 구조 검증은 유지하되 producer가 보장한
semantic scan은 반복하지 않으며 일반 runtime이나 external data가 선택할 수 없다.

`wavefront_event_count`는 terminal을 제외한 surface event 수다. Tape의 terminal
depth에 따라 Receiver/escaped는 surface event 수가 depth와 같고, blocked는
마지막 non-emitted surface를 포함하므로 `depth + 1`이다.

#### Ordered reducer와 원자성

`python_ordered_v1` reducer는 chunk 안에서 primary slot 오름차순으로 tape를
재생한다. Receiver grid/flux, optical/reflection/contribution과 dict key 생성,
stored-path quota/교체 순서는 `object_reference`와 동일하다. 저장 가능한 path만
`RayHit`로 materialize한다. Payload가 `omitted_v1`이면 reducer는 geometry column을
읽지 않는다.

Stop은 기존 primary chunk 원자성을 유지한다. Provider/planner 실패는 tape seal
전에 기존 whole-depth Python fallback과 circuit breaker로 처리하며 event 일부만
fallback 결과와 섞지 않는다. Compiled reducer나 event-level native fallback은
PERF-3B-2C 범위가 아니다.

Explicit SoA v2 경로가 실제 ROI p50을 object-reference `5.232795초`에서
`5.121246초`로 `1.021782x`, wall `2.132%` 개선했지만 자동 승격 gate
`>= 1.05x`에는 못 미쳤으므로 기본 `auto`는 `object_reference`다. GPU·Numba가
없는 PC의 기본 scalar/Python CPU 경로도 변경하지 않는다. 엄격한 교대가 아닌
`O,S,S,O,O,S` counterbalanced 순서의 여섯 measured run에서
semantic/grid/contribution/path hash는 exact했다. Intersection은 모두 effective
`numba_cpu`, `native_used=true`, attempt/success `1,078/1,078`, success row
`309,119`, fallback `0`이었다. Planner `auto`는 effective `python_cpu`,
logical/Python-sidecar `225,482/225,482`, native attempt/fallback `0`이었다.
100k paths-on tape peak는 `680,048 bytes`, run copy 회계는
`29,407,112 bytes`였다. 별도 10k paths-off/on A/B의 peak는
`271,080 / 643,800 bytes`, copy는 `1,131,996 / 2,952,580 bytes`였으며 이 값은
process RSS가 아니다.

### PERF-3B-2C-2 compiled ordered summary reducer

Runtime-only `wavefront_reducer` 허용값은 `auto`, `python_cpu`, `numba_cpu`다.
기본 `auto`는 `python_ordered_v1`이며 Numba를 import/probe하지 않는다. Explicit
`numba_cpu`는 `soa_event_tape`, `max_depth >= 2`, summary contribution에서만
`ordered_summary_reducer_v1`을 시도한다. Detailed contribution, scalar,
single-bounce, `object_reference`와 face/polygon legacy 경로는 정상 Python 선택이며
native attempt가 없다.

Native batch는 tape core/terminal/binding의 owned read-only input과 현재 public
summary에서 만든 owned mutable scratch accumulator로 구성한다. Serial
`float64`, `fastmath=False` kernel은 primary와 event의 기존 덧셈 순서를 보존한다.
Provider는 caller scratch를 직접 수정하지 않고 복사본에서 실행하며 output은
owned/C-contiguous/read-only, input과 non-alias여야 한다. Provider와 consumer가
count, float shadow/reference, touch order, storage와 digest를 모두 검증한 뒤
dict/grid/path를 별도 stage하고 마지막에 한 번만 publish한다.

Unavailable 또는 `initialize`, `execute`, `result_validation`, apply 준비 실패는
public 상태를 바꾸지 않고 같은 tape 전체를 Python으로 정확히 한 번 replay한다.
Run-local circuit breaker가 이후 native 시도를 막으며 logical tape/primary/event
count에는 attempt와 replay를 중복 반영하지 않는다. Stop은 기존 primary-chunk
원자성을 유지한다. Terminal-only tape는 primary count가 양수이고 event count가
`0`인 정상 input이다.

Actual ROI 100k, depth 10, paths 500, chunk 1,024의 warm p50은 Python reducer
`5.094436초`, native `4.643004초`로 `1.097228x`, wall `8.861%` 개선됐다.
Reducer replay는 `1.062883 -> 0.443459초`로 `2.3968x`, commit은
`1.101820 -> 0.643344초`로 `1.7126x`였다. Native attempt/success는
`98/98`, fallback `0`이고 seven semantic/hash family와 count가 exact했다.
Cold reducer JIT `2.382357초`와 optional package 조건 때문에 기본 `auto`는 계속
Python/no-probe이며 native reducer는 명시적 opt-in이다.

### PERF-3C strict-float64 CUDA와 hybrid dispatch

Project `compute_backend="gpu_cuda"`는 runtime `auto`를 batch 65,536,
intersection `gpu_cuda`, SoA event tape, `counter_rng_v2`, Numba planner와 Numba
summary reducer로 선택한다. CPU project와 구체적인 runtime override는 기존
정책을 유지한다. 특히 기본 `compute_backend="cpu"`는 Numba/CUDA를 import하거나
probe하지 않는다.

CUDA provider contract는 `strict_float64_bvh_v1`이다. Kernel은 `float64`,
`fastmath=False`이며 최종 face index, tie-break, traceable mask, `ignore_faces`,
`min_t`와 `max_t`를 CPU BVH와 동일하게 처리한다. Face/count/grid/summary는 exact,
distance와 stored-path geometry는 CPU/GPU FMA ULP 차이를 고려해 absolute/relative
`1e-12`로 비교한다. Host scene/input/output은 owned, C-contiguous, read-only,
non-alias여야 하고 execution metadata/timing도 consumer 검증을 통과해야 한다.

GPU project에서 active row가 strict `<8,192`인 intersection wave는
`hybrid_numba_cpu_small_wave_v1` 정책으로 Numba CPU에 보낸다. `8,192`는 GPU
경계다. Small-wave CPU 성공은 fallback이 아니다. CPU small-wave가 실패하면
GPU를 시도하고 해당 run의 hybrid CPU circuit을 연다. 이후 small wave는 같은
실패를 반복하지 않고 곧바로 GPU로 보낸다. Ray 수가 0인 direct provider 호출은
CUDA를 probe하지 않으며 owned/read-only empty result와 `not_probed` metadata를
반환한다.

GPU unavailable은 정상 CPU 선택으로 기록하고 hard fallback count를 올리지
않는다. `input_prepare`, `initialize`, `execute`, `result_validation` failure는
현재 logical batch의 일부 GPU 결과를 publish하지 않고 batch 전체를 CPU로 한 번
replay한다. 이후 run-local circuit breaker가 같은 run의 GPU 시도를 막는다.
Concurrent run은 breaker와 logical count를 공유하지 않는다.

Prepared device scene과 thread-local workspace는 재사용한다. Mesh acceleration
invalidation은 host/device scene cache를 함께 무효화한다. Workspace capacity는
다음 2의 거듭제곱으로 성장하므로 65,536은 launch/transfer에는 유리하지만
memory와 Stop 경계를 크게 한다. Stop은 시작한 primary/intersection chunk를
원자적으로 끝낸 다음 경계에서 반영한다.

### PERF-3D host-overhead와 run accumulator

Runtime-only `wavefront_reducer_commit` 허용값은 `auto`, `per_tape`,
`run_accumulator`다. GPU project의 `auto`는 `run_accumulator`, CPU project의
`auto`는 `per_tape`다. 기본 CPU/legacy project는 이 선택을 위해 Numba/CUDA를
import하거나 probe하지 않는다.

`run_accumulator`는 native ordered reducer의 strict `float64` numeric result를
run-local state로 유지한다. 성공 결과만 owned mutable clone으로 다음 tape에
넘기고 public dict/grid는 final flush 전까지 수정하지 않는다. Stored path는
tape별 완전한 staged copy 성공 뒤 원자 publish해 다음 quota/payload gate에
반영한다. 중간 tape가 실패하면 이전 성공 numeric state를 먼저 한 번 publish하고
failing logical tape 전체를 Python ordered reducer로 한 번 replay한다. Run-local
circuit breaker, no-double-count, Stop complete-chunk와 concurrent-run isolation
계약은 `per_tape`와 같다.

Reflection seed/Receiver numeric seam은 각각 `_wavefront_reflection_seeds()`와
`_find_first_receiver_hits_numeric()`이다. Scalar 함수는 exact oracle과
compatibility wrapper로 남는다. Effective dispatch metric은
`numpy_splitmix64_batch_v1`과 `numpy_numeric_batch_v2`다.

Path payload는 저장 quota가 비었거나 oldest dead-end replacement 가능성이 있으면
full이다. Quota가 receiver-only로 포화되면 run-local monotonic latch가 이후 tape를
omitted로 고정한다. 한 번 omitted로 전이한 뒤 full로 돌아갈 수 없다. Effective
payload는 `full_path_v1`, `omitted_v1`, `mixed_v1` 중 하나이고 requested mode와
full/omitted chunk·primary·event count, suppressed chunk count를 별도로 기록한다.

여기서 retained/resident는 CPU numeric accumulator에만 해당한다. Ray/scene 전체
GPU residency와 fused CUDA depth kernel은 이 계약의 범위 밖이다.

## 백엔드 종류

### `auto`
- triangle 수가 24개 이하이면 `brute_force`
- triangle 수가 25개 이상이면 `bvh`
- 일반 실행의 기본값

### `brute_force`
- 모든 triangle을 순서대로 검사한다.
- 가속 백엔드 정합성 검증용 reference
- 소형 synthetic geometry에서 구조 구축 비용 없이 사용

### `bvh`
- triangle bounds, edge, normal, centroid를 사전 계산한다.
- flat node 배열과 ordered face 배열을 사용한다.
- ray-AABB 판정 후 필요한 leaf triangle만 검사한다.
- 실제 STEP/STP CAD의 기본 가속 경로

## 정합성 규칙
- 가장 가까운 양의 `t`를 선택한다.
- 동일 거리에서 여러 face가 충돌하면 가장 작은 `face_index`를 선택한다.
- `ignore_face`, `min_t`, `max_t`는 모든 백엔드에서 동일하게 적용한다.
- normal 방향은 ray 진행 방향과 마주보도록 뒤집는다.
- backend가 달라도 `face_index`, `t`, `point`, `normal`이 허용 오차 내에서 동일해야 한다.

## 가속 데이터 무효화
- vertex 또는 face가 추가되면 prepared triangle과 BVH를 폐기한다.
- Transform 적용은 현재 새로운 `TriangleMesh`를 생성하므로 변경된 위치로 BVH가 다시 구축된다.
- 향후 mesh vertex를 직접 수정하는 API를 만들 경우 반드시 acceleration invalidation을 함께 호출해야 한다.

## RayTraceConfig
```json
{
  "compute_backend": "cpu",
  "intersection_backend": "auto"
}
```

- `compute_backend` 기본값: `cpu`
- `compute_backend` 허용값: `cpu`, `gpu_cuda`
- 기본값: `auto`
- 허용값: `auto`, `brute_force`, `bvh`
- UI 일반 사용자는 `auto`를 사용한다.
- GPU 가속은 project에서 `compute_backend="gpu_cuda"`로 명시한다.
- 필드가 없는 legacy `.bitsam` project는 `cpu`로 복원한다.
- 개발자 정합성 테스트에서만 강제 backend를 권장한다.

## 결과 기록
`RayTraceResult.metrics._performance_summary`에 다음 항목을 기록한다.

- `intersection_backend`
- `configured_intersection_backend`
- `bvh_node_count`
- `bvh_leaf_count`
- `bvh_build_sec`
- `rays_per_sec`
- `requested_intersection_dispatch`
- `intersection_batch_size`: 이번 run에 적용한 runtime chunk 크기
- `intersection_dispatch`: `scalar`, `batch`, 또는 혼합 실행의 `mixed`
- `intersection_batch_count`
- `intersection_batch_max_size`
- `intersection_ray_count`: 모든 active depth row를 포함한 전체 logical CAD
  query 수
- `intersection_scalar_query_count`
- `intersection_sec`: batch dispatch 호출만 합산한 시간
- `intersection_timing_scope`: 현재 `batch_dispatch_only`
- `native_batch`
- `requested_intersection_provider`
- `intersection_provider`: `python_cpu`, `numba_cpu`, `gpu_cuda`, `mixed`,
  `not_used`
- `reference_scalar_query_count`, `reference_batch_count`,
  `reference_batch_sec`
- `native_available`, `native_used`, `native_provider_version`,
  `native_provider_disabled`
- `native_attempt_count`, `native_attempt_ray_count`
- `native_success_count`, `native_success_ray_count`
- `native_scalar_success_count`, `native_batch_success_count`
- `native_scene_build_sec`, `native_jit_compile_sec`, `native_execute_sec`
- `intersection_fallback_count`, `intersection_fallback_ray_count`,
  `intersection_fallback_phase`, `intersection_fallback_reason`
- `intersection_provider_unavailable_reason`
- `compute_backend`
- `gpu_cuda_requested`, `gpu_cuda_available`, `gpu_cuda_used`
- `gpu_cuda_contract`, `gpu_cuda_strict_float64`
- `gpu_cuda_device_name`, `gpu_cuda_compute_capability`, `gpu_cuda_device_id`
- `gpu_cuda_numba_version`, `gpu_cuda_toolkit_layout`
- `gpu_cuda_scene_upload_sec`, `gpu_cuda_workspace_prepare_sec`,
  `gpu_cuda_input_upload_sec`, `gpu_cuda_kernel_sec`,
  `gpu_cuda_output_download_sec`
- `gpu_cuda_device_scene_reuse_count`, `gpu_cuda_workspace_reuse_count`
- `gpu_cuda_execution_policy`, `gpu_cuda_hybrid_cpu_below_rays`
- `gpu_cuda_hybrid_cpu_attempt_count`,
  `gpu_cuda_hybrid_cpu_attempt_ray_count`,
  `gpu_cuda_hybrid_cpu_success_count`,
  `gpu_cuda_hybrid_cpu_success_ray_count`,
  `gpu_cuda_hybrid_cpu_execute_sec`, `gpu_cuda_hybrid_cpu_failure_count`,
  `gpu_cuda_hybrid_cpu_failure_reason`, `gpu_cuda_hybrid_cpu_disabled`
- `gpu_cuda_gpu_attempt_count`, `gpu_cuda_gpu_attempt_ray_count`,
  `gpu_cuda_gpu_success_count`, `gpu_cuda_gpu_success_ray_count`
- `multi_bounce_wavefront_used`
- `requested_wavefront_pipeline`, `wavefront_pipeline`
- `wavefront_chunk_count`, `wavefront_primary_ray_count`
- `wavefront_depth_batch_count`, `wavefront_max_active_ray_count`,
  `wavefront_max_observed_depth`
- `wavefront_active_ray_count_by_depth`, `wavefront_batch_count_by_depth`,
  `wavefront_compacted_ray_count`
- `wavefront_receiver_dispatch`, `wavefront_reflection_rng`,
  `wavefront_rng_scalar_parity`, `wavefront_stochastic_primary_ray_count`
- `wavefront_surface_geometry_dispatch`, `wavefront_path_quota_dispatch`
- `wavefront_state_build_sec`, `wavefront_receiver_sec`,
  `wavefront_geometry_sec`, `wavefront_geometry_ray_count`,
  `wavefront_geometry_hit_count`, `wavefront_plan_sec`,
  `wavefront_commit_sec`, `wavefront_total_sec`
- `wavefront_path_materialized_count`,
  `wavefront_path_materialization_skipped_count`
- `wavefront_state_layout`, `wavefront_state_init_sec`,
  `wavefront_state_advance_sec`
- `wavefront_event_tape_contract`, `wavefront_event_tape_append_sec`,
  `wavefront_event_tape_seal_sec`, `wavefront_event_tape_validation_mode`,
  `wavefront_event_tape_validation_sec`
- `wavefront_event_tape_copy_bytes`, `wavefront_event_tape_copy_contract`,
  `wavefront_event_tape_path_payload`, `wavefront_event_tape_peak_scope`,
  `wavefront_event_count`, `wavefront_event_tape_peak_bytes`
- `wavefront_reducer_contract`, `wavefront_reducer_replay_sec`,
  `wavefront_reducer_hydrate_sec`,
  `wavefront_reducer_logical_event_count`
- `wavefront_reflection_seed_dispatch`
- `wavefront_event_tape_path_payload_requested`
- `wavefront_event_tape_path_payload_full_chunk_count`,
  `wavefront_event_tape_path_payload_omitted_chunk_count`,
  `wavefront_event_tape_path_payload_suppressed_chunk_count`
- `wavefront_event_tape_path_payload_full_primary_count`,
  `wavefront_event_tape_path_payload_omitted_primary_count`
- `wavefront_event_tape_path_payload_full_event_count`,
  `wavefront_event_tape_path_payload_omitted_event_count`
- `wavefront_reducer_commit_policy`,
  `wavefront_reducer_retained_tape_count`,
  `wavefront_reducer_retained_primary_count`,
  `wavefront_reducer_retained_event_count`
- `wavefront_reducer_final_flush_count`,
  `wavefront_reducer_final_flush_sec`,
  `wavefront_reducer_fallback_flush_count`
- `requested_wavefront_planner`, `wavefront_planner`,
  `wavefront_planner_contract`
- `wavefront_planner_logical_row_count`,
  `wavefront_planner_python_sidecar_row_count`
- `wavefront_planner_native_available`, `wavefront_planner_native_used`,
  `wavefront_planner_native_provider_version`,
  `wavefront_planner_native_provider_disabled`
- `wavefront_planner_native_attempt_count`,
  `wavefront_planner_native_attempt_row_count`,
  `wavefront_planner_native_success_count`,
  `wavefront_planner_native_success_row_count`
- `wavefront_planner_native_face_table_prepare_sec`,
  `wavefront_planner_native_input_prepare_sec`,
  `wavefront_planner_native_dispatch_sec`,
  `wavefront_planner_native_execute_sec`,
  `wavefront_planner_native_jit_compile_sec`
- `wavefront_planner_fallback_count`,
  `wavefront_planner_fallback_row_count`,
  `wavefront_planner_fallback_phase`, `wavefront_planner_fallback_reason`,
  `wavefront_planner_unavailable_reason`
- `wavefront_planner_rng_algorithm`, `wavefront_counter_apply_dispatch`

`wavefront_event_tape_validation_sec`은 seal과 plan에 포함되는 nested timing이다.
`builder_owned_materialization_v1` copy bytes와
`tape_owned_ndarray_estimate_v2` peak bytes는 tape-owned 배열 회계이며 process RSS,
allocator peak, GPU memory 또는 run 누적 memory를 의미하지 않는다. Copy bytes는
producer-side advanced-index gather를, peak bytes는 strict validator 임시 배열을
포함하지 않는다. Tape를 만들지 않는 경로에서는 validation/copy/payload/peak
scope가 `not_used`이고 관련 timing/byte/count는 `0`이다.

## 후속/외부 백엔드 확장 조건
- adapter는 동일 `HitRecord` 계약을 만족해야 한다.
- batch adapter는 동일 `RayBatch`/`RayHitBatch` 계약과 row 순서를 만족해야 한다.
- Embree/Open3D와 새로운 GPU kernel 결과를 reference와 자동 비교해야 한다.
- 외부 라이브러리가 없거나 초기화에 실패하면 `bvh`로 대체한다.
- Accelerator 실행 도중 실패하면 일부 결과를 섞지 않고 batch 전체를 CPU로 다시 실행한다.
- Accelerator primitive id는 최종 `mesh.faces` index로 remap하고 `ignore_faces`도 traversal 중 적용한다.
- Accelerator가 없는 PC에서도 프로젝트 실행과 CPU ray tracing이 가능해야 한다.
