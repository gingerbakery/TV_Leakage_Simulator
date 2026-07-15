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

- CAD 꼭지점 3개 또는 모서리 2개를 기준으로 빈 공간의 사각 발광면을 만든다.
- 선택한 vertex/edge ID는 편집 이력과 추후 연동을 위해 저장한다.
- 현재 V1은 등록 시 계산된 중심과 U/V 축을 ray tracer에 전달한다.

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
  "reference_vertex_indices": [],
  "reference_edge_vertex_indices": [],
  "ray_count": 10000,
  "seed": null,
  "enabled": true
}
```

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

- `three_vertices`: 서로 다른 vertex 3개를 선택한다.
- `two_edges`: 서로 다른 edge 2개를 선택한다.
- 3D viewer는 클릭한 triangle에서 클릭점과 가장 가까운 vertex 또는 edge ID를 반환한다.
- Emitter 선택 중에는 기존 emitter overlay가 picking을 가로막지 않도록 원본/이동 CAD 형상을 우선한다.

## 현재 제한

- Reference emitter는 등록 시 계산된 평면을 저장한다. 이후 기준 component가 다시 transform될 때 자동 추종하는 associativity는 후속 기능이다.
- Datum plane은 숫자 입력 기반이다. 3D gizmo를 이용한 직접 이동·회전은 후속 기능이다.
- V1 ray tracer는 가상 사각 평면 내부를 균일하게 sampling하며 분광과 색도는 사용하지 않는다.

