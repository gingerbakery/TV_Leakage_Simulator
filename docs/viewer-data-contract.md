# 3D Viewer 데이터 계약

## 목적
- Canvas 2D viewer에서 Three.js viewer로 넘어갈 때 기준이 되는 mesh/component/face 식별 규칙을 고정한다.
- ROI 선택, component 선택, transform preview, ray path overlay가 같은 ID 체계를 공유하도록 한다.

## 계약 이름
- `schema_version`: `mesh-scene.v1`

## 단위와 좌표계
- 길이 단위: `mm`
- 좌표계:
  - 오른손 좌표계
  - `x`, `y`, `z`는 CAD/model 원본 좌표를 따른다.
- Three.js 적용 시 좌표축 변환은 viewer 계층에서만 처리한다.
- 백엔드와 ray tracing 계층의 좌표는 원본 model 좌표를 유지한다.

## 최상위 payload 구조

```json
{
  "schema_version": "mesh-scene.v1",
  "units": {
    "length": "mm"
  },
  "coordinate_system": {
    "handedness": "right",
    "axes": {
      "x": "model_x",
      "y": "model_y",
      "z": "model_z"
    }
  },
  "mesh": {},
  "objects": [],
  "components": [],
  "metadata": {}
}
```

## Mesh 계약

### `mesh.vertices`
- 타입: `number[][]`
- 형식: `[[x, y, z], ...]`
- vertex id는 배열 index와 동일하다.

### `mesh.faces`
- 타입: `number[][]`
- 형식: `[[v0, v1, v2], ...]`
- triangle face id는 배열 index와 동일하다.

### `mesh.face_ids`
- 타입: `number[]`
- 형식: `[0, 1, 2, ...]`
- 명시적 face id 목록이다.
- 현재는 `mesh.faces` 배열 index와 동일해야 한다.

### `mesh.face_component_ids`
- 타입: `(number | null)[]`
- 각 face가 속한 component id를 제공한다.
- 길이는 `mesh.faces.length`와 같아야 한다.

### `mesh.face_material_ids`
- 타입: `string[]`
- 각 face의 material id를 제공한다.

### `mesh.face_normals`
- 타입: `number[][]`
- 각 face의 normal vector다.
- 형식: `[[nx, ny, nz], ...]`

### `mesh.face_centroids`
- 타입: `number[][]`
- 각 face의 centroid다.
- 형식: `[[x, y, z], ...]`

### `mesh.face_areas_mm2`
- 타입: `number[]`
- 각 face의 면적이다.

## Component 계약
- `objects`와 `components`는 현재 동일한 배열을 가리킨다.
- 기존 UI 호환성을 위해 `objects`를 유지한다.
- 신규 viewer는 `components` 이름을 우선 사용해도 된다.

각 component 항목:

```json
{
  "object_id": 0,
  "component_id": 0,
  "object_name": "Part 1",
  "component_name": "Part 1",
  "face_indices": [0, 1, 2],
  "face_count": 3,
  "area_mm2": 100.0,
  "bbox_min": [0, 0, 0],
  "bbox_max": [1, 1, 1],
  "is_truncated": false
}
```

## ID 규칙
- `vertex_id`: `mesh.vertices` 배열 index
- `face_id`: `mesh.faces` 배열 index
- `component_id`: `components[].component_id`
- 같은 scene payload 안에서는 ID가 안정적이어야 한다.
- CAD를 다시 import하면 ID가 다시 부여될 수 있다.

## Three.js viewer 적용 원칙
- `BufferGeometry` 생성 시:
  - position attribute는 `mesh.vertices`
  - index buffer는 `mesh.faces`
- face picking 시:
  - Three.js `faceIndex`를 `face_id`로 환산한다.
  - indexed geometry에서는 triangle 순서와 `mesh.faces` 순서가 유지되어야 한다.
- component highlight:
  - `mesh.face_component_ids[face_id]`를 기준으로 component를 찾는다.
- ROI highlight:
  - ROI는 항상 `face_id[]`로 표현한다.
- transform preview:
  - 대상은 component id 또는 face id 집합으로 표현한다.

- ROI skin의 재질·선택·CAD 면 광원 색상은 동일한 삼각형 geometry의 material group에 합성한다. 동일 위치에 polygon-offset mesh를 중첩하여 경계의 깊이 충돌을 재발시키지 않는다.
- ROI material group을 갱신할 때 position, normal, triangle 순서 및 `sourceFaceIds`를 변경하지 않는다. Picking과 ray tracing의 ID 계약은 표시 색상과 독립적이다.
- ROI cap의 빗금 표시와 원본 CAD 면의 선택은 분리한다. 가상 cap에 물성을 부여하기 위해 임의의 원본 face ID를 붙이지 않는다.

## 면별 Display Color (2026-09-10)
- 표시색 우선순위: `faceColorOverrides` → `componentColorOverrides` → CAD 원본색 → 기본 팔레트.
- `faceColorOverrides`는 `{ componentId, faceIds, color }[]` 형식이다. `faceIds`는 원본 CAD 면을 구성하는 scene 삼각형 ID의 집합이며, `color`는 `#rrggbb`이다.
- 색상 선택은 원본 CAD 면 전체에 적용한다. ROI에 해당 면 일부만 보여도 다른 ROI나 전체 뷰에서 동일한 면색을 유지한다. 빗금으로 표시하는 가상 절단면에는 적용하지 않는다.
- Full CAD는 기존 indexed geometry의 vertex color, ROI는 기존 skin의 material group을 사용한다. 색상만을 위한 coplanar overlay나 삼각형 재분할을 추가하지 않는다.
- 선택/광원 하이라이트는 표시색 위에 일시적으로 표시할 수 있으나 저장된 면색을 변경하지 않는다.
- Components의 색상 변경은 부품 기본색을 바꾼다. 면별 지정색은 유지하며, 면 팔레트의 `부품 기본색으로 되돌리기`로 개별 해제한다.
- `.bitsam`과 Case 상태에 색상을 저장한다. 구버전의 필드 누락은 빈 목록이다. 다른 CAD에 설정만 불러오기/Copy Setup에서는 면 ID를 검증할 수 없으므로 면색을 복사하지 않는다.

## 현재 제한
- component id는 scene payload 내부에서만 안정적이다.
- STEP 원본의 assembly 이름이나 CAD feature 이름은 아직 보존하지 않는다.
- 큰 CAD에서는 face별 normal/centroid/area 배열이 payload 크기를 키울 수 있다.

## 다음 단계
1. 현재 Canvas 2D viewer가 새 필드를 무시하고 기존 방식으로 동작하는지 확인
2. Three.js viewer에서 `mesh-scene.v1` payload를 읽어 렌더링
3. face/component picking을 `face_id`, `component_id` 기준으로 연결
4. ROI/transform/material selection state를 같은 ID 계약으로 통합
