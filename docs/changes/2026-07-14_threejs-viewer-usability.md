# 2026-07-14 Three.js viewer 사용성 개선

## 배경
- Three.js viewer가 기본 viewer로 안정화되면서 Canvas 전환 UI가 불필요해졌다.
- 3D viewer 내 XYZ 좌표계가 얇고 작아 모델 조작 중 방향 인지가 어려웠다.
- `XY`, `-XY` 등 camera preset이 첫 클릭에서는 기울어진 상태처럼 보이고, 두 번째 클릭 후에야 고정되는 현상이 있었다.

## 변경 사항
- Canvas 전환 버튼을 UI에서 제거했다.
- Three.js viewer를 기본 viewer로 고정했다.
- XYZ 좌표계를 커스텀 axis triad로 교체했다.
  - X: red
  - Y: green
  - Z: blue
  - 축 shaft, cone head, label badge를 추가했다.
- Axis size slider가 Three.js axis triad 크기에 직접 반영되도록 연결했다.
- Camera preset 적용 시 OrbitControls damping 영향을 일시적으로 제거하고, camera position/up/target/lookAt을 한 번에 고정하도록 수정했다.
- viewer 안내 문구를 ROI/클릭 중심에서 camera/조작 중심으로 정리했다.

## 검증
- `MODULE_3_Z27_HELICAL_GEAR_SAG.stp` 기준 자동 브라우저 검증 수행
  - Canvas 버튼 없음 확인
  - Three.js engine 고정 확인
  - axis triad child count: `9`
  - `XY` 1회 클릭과 2회 클릭 후 camera position/up/target 동일 확인
  - `-XY` 1회 클릭과 2회 클릭 후 camera position/up/target 동일 확인

## 비고
- `XY`는 모델 좌표계 기준 `+Z` 방향에서 바라보는 view다.
- 실제 CAD에서 사용자가 기대하는 정면이 `XY`가 아닐 수 있으므로, 추후 `Front/Back/Left/Right` 명칭과 CAD 좌표계 기준 명칭을 함께 정리할 수 있다.

## 2026-07-14 추가 수정 (`v0.7.3`)
- XYZ axis label의 원형 badge 배경을 제거하고, `X`, `Y`, `Z` 글자만 표시하도록 변경했다.
- Transform `Apply` 시 hidden face 상태가 바뀌어 mesh가 재생성되더라도 기존 camera position/up/target을 유지하도록 수정했다.
- Helical Gear STP 기준으로 `XY` view 상태에서 transform apply 후 camera position/up/target이 변하지 않음을 확인했다.
