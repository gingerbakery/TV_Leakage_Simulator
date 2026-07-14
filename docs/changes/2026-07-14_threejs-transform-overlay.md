# 2026-07-14 Three.js transform overlay 적용

## 배경
- Three.js viewer 전환 후 CAD mesh 표시와 카메라 조작은 가능해졌지만, transform preview/applied 결과는 기존 Canvas viewer에만 반영되고 있었다.
- Component move/tilt가 3D viewer에서 직접 확인되어야 gap 생성 작업을 CAD 프로그램처럼 사용할 수 있다.

## 변경 사항
- Three.js viewer에 transform overlay layer를 추가했다.
- Apply 전 입력값은 노란색 preview overlay로 표시한다.
- Apply 후 transform rule은 빨간색 applied overlay로 표시한다.
- Apply된 component의 원래 위치 face는 기본 mesh에서 숨기고, 이동/tilt된 위치의 component만 overlay로 표시한다.
- ROI viewer에서도 선택 ROI와 겹치는 transform overlay만 표시되도록 구성했다.
- 기존 Canvas viewer는 fallback으로 유지한다.

## 동작 기준
- `component_move_gap`
  - Transform popup에서 move/tilt 입력 시 preview overlay 표시
  - `Apply` 후 transform rule에 반영
  - applied component는 빨간색으로 유지 표시
  - 원래 위치의 component face는 숨김 처리
- `face_gap`
  - 선택 face 집합에 대해 preview overlay를 표시
  - 현재 단계에서는 component transform rule처럼 영구 applied rule로 분리하지 않는다.

## 검증
- `MODULE_3_Z27_HELICAL_GEAR_SAG.stp` import 후 Three.js viewer에서 검증
  - vertices: `7653`
  - faces: `9486`
  - objects/components: `284`
- 검증 시나리오:
  - component transform 시작
  - `X = 8 mm`, `Rz = 18 deg` 입력
  - Apply 전 preview overlay 생성 확인
  - Apply 후 applied overlay 생성 확인
  - 적용 대상 원본 face가 기본 mesh에서 제외되는 것 확인
- 자동 브라우저 캡처에서 기어 component 이동/tilt 표시 확인.

## 다음 단계
1. Three.js raycaster로 component/face picking을 연결한다.
2. Component tree 선택과 viewer highlight를 Three.js 기준으로 동기화한다.
3. Transform popup 위치/선택 UX를 Three.js picking 기준으로 정리한다.
4. 여러 component transform rule의 A/B 비교와 scenario 저장 구조를 정리한다.
