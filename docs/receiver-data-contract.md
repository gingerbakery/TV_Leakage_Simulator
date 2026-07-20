# Receiver 데이터 계약

## 목적

- UI, Three.js viewer, ray tracer가 동일한 Receiver 정의를 사용하도록 입력 구조를 고정한다.
- Receiver를 단순 좌표점이 아니라 **위치·방향·크기·수광 조건을 가진 가상 측정면**으로 정의한다.
- 향후 휘도 히트맵, 관측자 시점 렌더링, 설계안 A/B 비교가 같은 Receiver ID를 기준으로 연결되도록 한다.

## V1 Receiver 형상

- 형상은 직사각형 평면(`rectangle`)만 지원한다.
- 평면 좌표계는 `center`, `u_axis`, `v_axis`, `normal`로 정의한다.
- `width_mm`는 U축 방향 길이, `height_mm`는 V축 방향 길이이다.
- `resolution`은 `(U 방향 bin 수, V 방향 bin 수)`이며, ray hit를 2D 히트맵으로 누적할 때 사용한다.
- 수광 normal은 광원이 있는 방향을 향하도록 설정한다. 입사 광선과 normal의 방향 관계로 수광 여부를 판정한다.

## 배치 방식

### Datum plane

- CAD 형상과 관계없이 빈 공간에 Receiver를 배치한다.
- 중심 X/Y/Z, Width/Height, Rx/Ry/Rz를 사용자가 직접 입력한다.
- 휘도계 위치나 고정 관측 위치를 수치로 정의할 때 사용한다.

### Reference geometry

- CAD vertex 3~6개 또는 edge 2개를 선택해 형상과 정렬된 가상 측정면을 만든다.
- 선택한 reference ID를 함께 저장해 설계 이력과 재현성을 확보한다.
- vertex 방식은 최소 3개에서 평면을 생성하며 최대 6개까지 선택할 수 있다.
- 다중 vertex는 가장 멀리 떨어진 두 점으로 U축을 잡고, 나머지 점 중 U축에서 가장 멀리 떨어진 방향으로 V축을 계산한다.
- 잘못 선택한 점은 다시 클릭해 제외하거나 `Clear selected points`로 한 번에 초기화한다.
- 기준면 생성 후 `position_offset_mm`과 `tilt_xyz_deg`로 월드 X/Y/Z 이동 및 회전을 추가 적용할 수 있다.
- 현재 V1은 등록 시 계산된 중심과 U/V축을 저장한다. 이후 component transform을 자동 추종하는 associativity는 후속 기능이다.

### Current view

- Full CAD View의 현재 카메라 방향과 화면 수평/수직 방향을 Receiver 좌표계로 저장한다.
- `view_distance_mm`만큼 카메라 target에서 떨어진 위치에 측정면을 배치한다.
- 사용자가 현재 보고 있는 각도에서 빛샘을 빠르게 평가할 때 사용한다.
- 카메라 기준면을 생성한 뒤 `position_offset_mm`과 `tilt_xyz_deg`로 위치와 수광 방향을 미세 조정할 수 있다.

## JSON 구조

```json
{
  "receiver_id": "receiver_001",
  "receiver_type": "rectangle",
  "display_name": "Lower corner observer",
  "placement_mode": "current_view",
  "center": [260.0, 150.0, 177.5],
  "normal": [0.0, 0.0, -1.0],
  "u_axis": [1.0, 0.0, 0.0],
  "v_axis": [0.0, -1.0, 0.0],
  "width_mm": 100.0,
  "height_mm": 30.0,
  "resolution": [80, 24],
  "acceptance_angle_deg": 90.0,
  "normal_flip": false,
  "reference_mode": null,
  "reference_vertex_indices": [],
  "reference_edge_vertex_indices": [],
  "view_distance_mm": 130.0,
  "base_center": [260.0, 150.0, 177.5],
  "base_u_axis": [1.0, 0.0, 0.0],
  "base_v_axis": [0.0, -1.0, 0.0],
  "base_normal": [0.0, 0.0, -1.0],
  "position_offset_mm": [0.0, 0.0, 5.0],
  "tilt_xyz_deg": [0.0, 3.0, 0.0],
  "enabled": true
}
```

## 필드 규칙

- `receiver_id`: 프로젝트 내 고유하고 변경되지 않는 ID이다.
- `display_name`: UI 표시 이름이며 사용자가 변경할 수 있다.
- `placement_mode`: `datum_plane`, `reference_plane`, `current_view` 중 하나이다.
- `center`: 월드 좌표계 기준 mm 단위이다.
- `u_axis`, `v_axis`, `normal`: 정규화된 직교 좌표계이다. 백엔드는 U/V축을 다시 직교화하고 normal 방향과 일치시킨다.
- `width_mm`, `height_mm`: 0보다 커야 한다.
- `resolution`: 두 값 모두 양의 정수여야 한다.
- `acceptance_angle_deg`: `(0, 180]` 범위이다.
- `normal_flip`: UI에서 기본 수광 방향을 반전했는지 기록한다. ray tracer에 전달되는 `normal`은 반전이 적용된 최종 방향이다.
- `view_distance_mm`: Current view 방식에서만 사용하며 0보다 커야 한다.
- `base_center`, `base_u_axis`, `base_v_axis`, `base_normal`: Reference/Current view의 추가 변환 전 기준면이다.
- `position_offset_mm`: 기준면 중심에 더하는 월드 X/Y/Z 이동량이며 단위는 mm이다.
- `tilt_xyz_deg`: 기준면의 U/V축에 X→Y→Z 순서로 적용하는 월드축 회전량이며 단위는 degree이다.
- 최종 `center`, `u_axis`, `v_axis`, `normal`에는 위 추가 변환이 이미 적용되어 있으며 ray tracer는 최종값을 사용한다.

## 좌표계와 ray hit

- Receiver 평면과 ray의 교차점을 계산한 뒤 중심 기준 U/V 좌표로 변환한다.
- U/V 좌표가 각각 Width/Height 절반 범위 안에 있을 때만 hit로 인정한다.
- 입사각은 `-dot(ray_direction, receiver.normal)`을 기준으로 계산한다.
- acceptance angle을 벗어난 ray는 Receiver 평면을 통과하더라도 수광하지 않는다.
- hit energy는 `resolution`에 따라 분할된 2D bin에 누적한다.

## 현재 제한사항

- Receiver는 직사각형 평면만 지원하며 원형 센서와 곡면 Receiver는 후속 범위이다.
- Reference geometry Receiver는 기준 component가 나중에 transform되어도 자동 추종하지 않는다.
- Current view Receiver는 `Update from current view` 또는 신규 생성 시점의 카메라를 snapshot으로 저장한다.
- V1 UI는 Receiver 계약과 3D 배치 및 Emitter → Receiver direct ray tracing까지 연결한다.
- Direct mode에서는 CAD 차폐와 반사/산란을 아직 계산하지 않으며 RT-2에서 추가한다.
