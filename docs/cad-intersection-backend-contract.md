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

### 최초 CPU adapter

PERF-3B 계약 단계의 `intersect_rays()`는 각 row를 기존 scalar
`intersect_ray()`에 위임한다. 따라서 현재는 `native_batch=false`이며
속도 향상 구현이 아니다. 이 reference adapter로 scalar/batch 정합성을
고정한 뒤 동일한 입출력 경계 뒤에 native CPU 또는 GPU 구현을 연결한다.

backend와 dispatch는 구분한다.

- backend: `auto`, `brute_force`, `bvh`처럼 교차를 계산하는 구현
- dispatch: 한 ray를 호출하는 `scalar`, 여러 ray를 전달하는 `batch`

이번 단계에서는 `RayTraceConfig.intersection_backend` 허용값과 자동 선택
정책을 변경하지 않는다. `gpu_cuda`는 실제 adapter, capability probe,
whole-batch CPU fallback이 준비되는 단계에서만 공개한다.

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

## 향후 백엔드 확장 조건
- adapter는 동일 `HitRecord` 계약을 만족해야 한다.
- batch adapter는 동일 `RayBatch`/`RayHitBatch` 계약과 row 순서를 만족해야 한다.
- Embree/Open3D/GPU 결과를 `brute_force`와 자동 비교하는 테스트가 필요하다.
- 외부 라이브러리가 없거나 초기화에 실패하면 `bvh`로 대체한다.
- GPU 실행 도중 실패하면 일부 GPU 결과와 CPU 결과를 섞지 않고 batch 전체를 CPU로 다시 실행한다.
- GPU primitive id는 최종 `mesh.faces` index로 remap하고 `ignore_faces`도 traversal 중 적용한다.
- GPU가 없는 PC에서도 프로젝트 실행과 CPU ray tracing이 가능해야 한다.
