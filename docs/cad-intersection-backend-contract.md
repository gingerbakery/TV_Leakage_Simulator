# CAD 교차 백엔드 데이터 계약

## 목적
- ray tracing 엔진이 CAD 교차 구현 방식과 분리되도록 한다.
- brute-force, BVH, 향후 Embree/Open3D/GPU 백엔드가 동일한 교차 결과 형식을 반환하도록 한다.
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
건드리지 않는다. 생성된 origin/direction 배열만 별도의 기본 4,096-ray
intersection chunk로 잘라 primary와 secondary query에 전달한다.

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

face/polygon emitter와 `max_depth >= 2`는 reflection RNG의 depth-first 소비
순서를 유지하기 위해 기존 scalar dispatch를 사용한다. runtime dispatch와
chunk 크기는 테스트/benchmark용 keyword-only 인자이며 프로젝트 JSON이나
`.bitsam`에는 저장하지 않는다.

Stop은 다음 sampling batch를 만들기 전과 intersection chunk 경계에서
확인한다. 시작한 chunk는 primary, secondary와 결과 commit까지 원자적으로
완료하고 다음 chunk를 시작하지 않는다. 기본값에서 최악의 Stop 지연은
4,096 primary ray 처리 시간이다.

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
  "intersection_backend": "auto"
}
```

- 기본값: `auto`
- 허용값: `auto`, `brute_force`, `bvh`
- UI 일반 사용자는 `auto`를 사용한다.
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
- `intersection_dispatch`: `scalar`, `batch`, 또는 혼합 실행의 `mixed`
- `intersection_batch_count`
- `intersection_batch_max_size`
- `intersection_ray_count`: primary/secondary를 포함한 전체 CAD query 수
- `intersection_scalar_query_count`
- `intersection_sec`: batch dispatch 호출만 합산한 시간
- `intersection_timing_scope`: 현재 `batch_dispatch_only`
- `native_batch`
- `requested_intersection_provider`
- `intersection_provider`: `python_cpu`, `numba_cpu`, `mixed`, `not_used`
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

## 향후 백엔드 확장 조건
- adapter는 동일 `HitRecord` 계약을 만족해야 한다.
- batch adapter는 동일 `RayBatch`/`RayHitBatch` 계약과 row 순서를 만족해야 한다.
- Embree/Open3D/GPU 결과를 `brute_force`와 자동 비교하는 테스트가 필요하다.
- 외부 라이브러리가 없거나 초기화에 실패하면 `bvh`로 대체한다.
- GPU 실행 도중 실패하면 일부 GPU 결과와 CPU 결과를 섞지 않고 batch 전체를 CPU로 다시 실행한다.
- GPU primitive id는 최종 `mesh.faces` index로 remap하고 `ignore_faces`도 traversal 중 적용한다.
- GPU가 없는 PC에서도 프로젝트 실행과 CPU ray tracing이 가능해야 한다.
