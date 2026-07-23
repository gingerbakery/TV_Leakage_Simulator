# Emitter 데이터 계약

## 목적

- UI, 3D viewer, ray tracer가 동일한 광원 정의를 사용하도록 입력 구조를 고정한다.
- CAD 형상 위의 광원과 빈 공간의 가상 광원을 같은 `EmitterSpec`으로 전달한다.
- 기구 개발자가 광원 방식과 power 기준을 바꾸더라도 ray tracer 코드를 직접 수정하지 않도록 한다.

## 지원 광원

### CAD surface emitter

- 실제 CAD triangle surface를 발광면으로 사용한다.
- `face_indices`에 하나 이상의 face ID를 전달한다.
- 발광 방향은 선택 surface의 면적 가중 평균 normal을 사용한다.

### Datum plane emitter

- CAD 형상이 없는 빈 공간에 가상의 사각 발광면을 만든다.
- 중심 좌표, 폭, 높이, 로컬 U/V 축으로 위치와 방향을 정의한다.
- UI에서는 중심 X/Y/Z, Width/Height, Rx/Ry/Rz를 입력하고 U/V 축으로 변환한다.

### Reference geometry emitter

- CAD 꼭지점 3~6개 또는 모서리 2개를 기준으로 빈 공간의 발광면을 만든다.
- 선택한 vertex/edge ID는 편집 이력과 추후 연동을 위해 저장한다.
- 현재 V1은 등록 시 계산된 중심과 U/V 축을 ray tracer에 전달한다.
- Vertex 방식은 `rectangular_fit`과 `polygon_auto` 두 생성 방식을 지원한다.

## 공통 JSON 구조

```json
{
  "emitter_id": "emitter_001",
  "emitter_type": "datum_plane",
  "face_indices": [],
  "normal_mode": "custom",
  "normal_flip": false,
  "custom_normal": [0.0, 0.0, 1.0],
  "direction_distribution": "lambertian",
  "gaussian_sigma_deg": 12.0,
  "power_mode": "power_per_area",
  "power_lumen": 1.0,
  "power_density_lm_per_m2": 250.0,
  "center": [260.0, 150.0, 47.5],
  "u_axis": [1.0, 0.0, 0.0],
  "v_axis": [0.0, 1.0, 0.0],
  "width_mm": 20.0,
  "height_mm": 20.0,
  "reference_mode": null,
  "surface_construction": "rectangular_fit",
  "polygon_vertices": [],
  "reference_vertex_indices": [],
  "reference_edge_vertex_indices": [],
  "reference_vertex_points": [],
  "reference_edge_points": [],
  "ray_count": 10000,
  "seed": null,
  "enabled": true
}
```

## ROI 절단 Reference 좌표 계약

- `reference_vertex_points`는 원본 CAD vertex와 ROI 절단으로 새로 생긴 가상 vertex의 월드 좌표를 함께 저장한다.
- `reference_edge_points`는 원본 CAD edge와 ROI 절단 경계 edge의 양 끝점 좌표를 저장한다.
- 원본 mesh index가 존재하면 기존 `reference_vertex_indices`, `reference_edge_vertex_indices`도 호환용으로 함께 저장한다.
- ROI 절단 vertex/edge처럼 원본 index가 없는 경우에도 좌표 필드를 기준으로 Emitter 평면을 재생성할 수 있다.

## Power 모드

### Total power

- 입력 단위는 lumen이다.
- ray 1개당 power는 `power_lumen / ray_count`로 계산한다.

### Power per area

- 입력 단위는 `lm/m²`이다.
- 발광면 면적을 `mm²`에서 `m²`로 변환한 후 총 power를 계산한다.
- 계산식은 `total_lumen = power_density_lm_per_m2 × area_mm2 × 1e-6`이다.
- ray 1개당 power는 `total_lumen / ray_count`이다.

## 방향 분포

- `lambertian`: surface normal 기준 cosine-weighted 반구 분포이며 기본값이다.
- `isotropic`: 전 방향 균일 분포이다.
- `gaussian`: surface normal 주위의 Gaussian 각도 분포이다.
- `normal_flip=true`이면 기본 normal의 반대 방향으로 방출한다.

## Reference 선택 규칙

- `three_vertices`: 호환성을 위해 유지하는 계약 이름이며, UI에서는 서로 다른 vertex 3~6개를 선택한다.
- `two_edges`: 서로 다른 edge 2개를 선택한다.
- vertex는 3개부터 평면 preview를 생성하고 최대 6개까지 선택한다.
- 선택점이 6개이면 추가 선택을 자동 교체하지 않는다. 기존 점을 다시 클릭해 제외하거나 `Clear selected points`로 명시적으로 초기화한다.
- 3D viewer는 클릭한 triangle에서 클릭점과 가장 가까운 vertex 또는 edge ID를 반환한다.
- Emitter 선택 중에는 기존 emitter overlay가 picking을 가로막지 않도록 원본/이동 CAD 형상을 우선한다.

## Reference 면 생성 규칙

### Plane containing vertices

- 계약값은 `surface_construction="rectangular_fit"`이다.
- 선택점을 포함하도록 로컬 U/V 좌표의 최소·최대 범위를 계산해 사각 발광면을 만든다.
- 발광 면적과 `power_per_area` 계산에는 `width_mm × height_mm`를 사용한다.

### Polygon – Auto closed boundary

- 계약값은 `surface_construction="polygon_auto"`이다.
- 선택 순서와 무관하게 점을 계산 평면에 투영하고 2D convex hull로 외곽 폐곡선을 자동 생성한다.
- 외곽선 내부에 있는 선택점과 같은 선 위의 중간점은 polygon 꼭지점에서 제외될 수 있으며 UI에 제외 개수를 표시한다.
- 선택점의 계산 평면 이탈 오차가 `0.05 mm`를 넘으면 UI에서 Apply를 차단한다.
- 저장되는 `polygon_vertices`는 자동 정렬되고 계산 평면 위로 투영된 3D 좌표이다.
- ray tracer는 convex polygon을 삼각형 fan으로 나누고 면적 가중 방식으로 선택한 삼각형 내부에서 ray 시작점을 균일 샘플링한다.
- 발광 면적과 `power_per_area` 계산에는 bounding rectangle이 아니라 실제 polygon 면적을 사용한다.

## 현재 제한

- Reference emitter는 등록 시 계산된 평면을 저장한다. 이후 기준 component가 다시 transform될 때 자동 추종하는 associativity는 후속 기능이다.
- Datum plane은 숫자 입력 기반이다. 3D gizmo를 이용한 직접 이동·회전은 후속 기능이다.
- V1 ray tracer는 사각 또는 convex polygon 발광면 내부를 균일하게 sampling하며 분광과 색도는 사용하지 않는다.
