# PERF-2 CAD 교차 가속

## 목적
- 실제 STEP/STP에서 triangle 수가 증가할 때 발생하는 ray-scene intersection 병목을 줄인다.
- 광학 결과와 face/component/material 연결을 유지한다.
- 외부 가속 라이브러리 없이도 회사 PC에서 동작하는 CPU 기본 가속 경로를 제공한다.

## 기존 구조 점검
- 기존 `TriangleMesh`에도 재귀형 BVH가 존재했다.
- 그러나 다음 계산이 반복됐다.
  - triangle edge와 normal 계산
  - triangle bounds와 centroid 계산
  - traversal 중 child list 생성과 정렬
  - hit 후보마다 `HitRecord` 생성
- 최초 flat BVH 시도에서 매 ray마다 leaf 통계를 다시 계산하는 병목을 발견하고 제거했다.

## 구현 내용

### Prepared triangle
각 triangle에 대해 한 번만 계산한다.

- `v0`
- `edge1`, `edge2`
- normal
- bounds min/max
- centroid

### Flat BVH
- 재귀 object tree 대신 flat node 배열을 사용한다.
- leaf는 ordered face 배열의 `start/count`만 저장한다.
- 가까운 child를 먼저 검사하고 현재 최단 거리보다 먼 node는 제외한다.
- leaf 크기는 실제 기어 STEP 측정 결과가 가장 좋았던 `8`을 유지한다.

### Hit 객체 최소화
- triangle 검사 중에는 거리와 face index만 갱신한다.
- 최종 가장 가까운 face에 대해서만 `HitRecord`를 생성한다.

### Backend 계약
- `auto`
- `brute_force`
- `bvh`

`RayTraceConfig.intersection_backend`로 지정하며 기본값은 `auto`다.

### 동률 처리
- 공유 edge 또는 vertex에서 동일 거리의 face가 여러 개 검출되면 가장 작은 `face_index`를 선택한다.
- Face Override 조회가 backend traversal 순서 때문에 달라지는 문제를 방지한다.

### Web 결과
- Web UI 버전을 `v0.9.10`으로 변경했다.
- Result KPI에 실제 `CAD intersection` backend를 표시한다.
- BVH 구축 시간을 결과 설명에 표시한다.

## 검증
- 전체 unit test: `34개` 통과
- 신규 BVH 검증:
  - random ray brute-force/BVH 비교
  - `ignore_face`
  - `max_t`
  - mesh 변경 후 BVH 재구축
  - auto backend 선택
  - 공유 edge 동일 face index 선택
- 실제 CAD reference ray mismatch: `0`

## 성능 결과

### TV 조립 샘플
- triangle: `116`
- brute-force: 약 `19,135 ray/s`
- 기존 recursive BVH: 약 `21,767 ray/s`
- PERF-2 flat BVH: 약 `38,983 ray/s`
- 기존 BVH 대비: 약 `1.79배`

### Helical Gear STEP
- triangle: `9,486`
- brute-force: 약 `219 ray/s`
- 기존 recursive BVH: 약 `4,972 ray/s`
- PERF-2 flat BVH: 약 `19,099 ray/s`
- brute-force 대비: 약 `87.1배`
- 기존 BVH 대비: 약 `3.84배`
- BVH 구축: 약 `0.213초`

## 관련 파일
- `src/leakage_simulator/geometry.py`
- `src/leakage_simulator/types.py`
- `src/leakage_simulator/raytracer.py`
- `tests/test_geometry_bvh_perf2.py`
- `tests/test_raytracer_rt2a.py`
- `scripts/benchmark_perf2_intersections.py`
- `docs/cad-intersection-backend-contract.md`
- `outputs/perf2_intersection_benchmark/summary.json`
- `outputs/perf2_intersection_benchmark/perf2_intersection_throughput.png`

## 실행 명령
```powershell
.\_tools\python313\python.exe -m unittest discover -s tests -p 'test_*.py'
.\_tools\python313\python.exe scripts\benchmark_perf2_intersections.py
```

## GPU가 없는 PC
- PERF-2는 CPU에서 동작하므로 GPU가 없어도 동일하게 사용할 수 있다.
- CAD import, 3D viewer, Transform, Material, Emitter, Receiver와 ray tracing이 모두 사용 가능하다.
- 향후 GPU backend를 추가하더라도 flat BVH CPU 경로를 fallback으로 유지한다.

## 다음 단계
- 실제 TV ROI 도면으로 end-to-end ray tracing 시간을 측정한다.
- triangle 수, ray 수, 반사 depth별 목표 시간을 정한다.
- 목표에 미달할 경우 Embree/Open3D adapter를 비교한다.
- CPU 가속 후에도 부족할 때만 OptiX/CUDA GPU backend를 검토한다.
