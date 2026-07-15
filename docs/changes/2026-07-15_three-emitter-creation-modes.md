# Web UI v0.7.18 - 세 가지 Emitter 생성 방식

## 변경 목적

- 실제 CAD surface뿐 아니라 기구 내부의 빈 공간에서도 광원을 배치할 수 있게 한다.
- 광원 면을 CAD 꼭지점/모서리로 정의하는 기구 개발자 친화적 방식을 제공한다.
- 설계 조건에 따라 총 광속과 면적당 광속을 모두 사용할 수 있게 한다.

## UI 변경

- Emitter 생성 메뉴에 다음 세 방식을 추가했다.
  - `CAD surface emitter`
  - `Datum plane emitter`
  - `Reference geometry emitter`
- 각 생성 방식 옆에 `?` 도움말을 배치하고 hover/focus 시 한글 설명을 표시한다.
- Emitter popup에서 `Total power`와 `Power per area`를 선택할 수 있다.
- Datum plane 입력에 Center X/Y/Z, Width/Height, Rx/Ry/Rz를 추가했다.
- Reference geometry는 `3 vertices`와 `2 edges` 선택 방식을 제공한다.
- 선택한 가상 평면, reference marker/edge, 방출 normal을 Three.js overlay로 표시한다.
- 등록된 emitter 목록에서 광원 종류, 크기 또는 face 수, 방향 분포, power 기준을 확인할 수 있다.

## 백엔드 변경

- `EmitterSpec`에 `datum_plane`, `reference_plane` 타입을 추가했다.
- 가상 평면용 중심, U/V 축, 폭, 높이와 reference ID 필드를 추가했다.
- `power_mode`와 `power_density_lm_per_m2`를 추가했다.
- ray tracer가 사각 가상 평면 내부에서 ray origin을 균일 sampling하도록 확장했다.
- 면적당 power는 발광면 면적을 이용해 총 lumen으로 환산한 뒤 ray별 power로 나눈다.

## Picking 보정

- Reference 선택 중 기존 emitter highlight가 CAD 클릭을 가로막던 문제를 수정했다.
- Emitter 선택 모드에서는 emitter overlay를 제외하고 원본 또는 transform된 CAD 형상을 기준으로 vertex/edge를 찾는다.

## 검증

- Python 문법 검사: 통과
- 렌더된 JavaScript `node --check`: 통과
- 전체 단위 테스트 4건: 통과
- 브라우저 검증:
  - Datum plane 미리보기 및 저장: 통과
  - Power per area payload 저장: 통과
  - CAD surface 다중 surface 선택 및 저장: 통과
  - Reference vertex 3개 선택, 가상 평면 생성 및 저장: 통과

## 후속 작업

- component transform 이후 Reference emitter 자동 추종
- Datum/Reference plane용 3D move/rotate gizmo
- Receiver CAD식 배치 UI와 RT-1 실행 연결
