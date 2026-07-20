# Web UI v0.8.0 - Receiver 데이터 계약 및 배치 UI

## 변경 목적

- ray tracing의 다음 단계인 Receiver를 CAD식 조작 흐름으로 정의한다.
- 관측 위치를 숫자 입력뿐 아니라 CAD reference와 현재 카메라 시점으로도 빠르게 배치할 수 있도록 한다.
- 향후 휘도 히트맵과 관측자 시점 렌더링에 사용할 Receiver ID 및 평면 좌표계를 고정한다.

## 데이터 계약 변경

- `ReceiverSpec`에 표시 이름과 배치 방식 필드를 추가했다.
- 명시적인 U/V축, normal 반전, reference ID, current-view 거리를 저장하도록 확장했다.
- U/V축은 백엔드에서 정규화·직교화하고 요청 normal과 같은 방향을 갖도록 보정한다.
- ray tracer의 Receiver frame 생성이 명시적 U/V축을 우선 사용하도록 변경했다.

## UI 변경

- Ray tracing > Receiver에 세 가지 생성 방식을 추가했다.
  - `Datum plane receiver`
  - `Reference geometry receiver`
  - `Current view receiver`
- 각 방식 옆의 `?` 도움말로 기구개발자가 배치 방식의 차이를 확인할 수 있다.
- Receiver properties 팝업에서 이름, 크기, 위치/회전, 해상도, acceptance angle, normal 반전을 입력한다.
- 팝업은 3D viewer 안에서 드래그해 이동할 수 있다.
- 등록된 Receiver는 왼쪽 목록에서 다시 선택해 편집하거나 삭제할 수 있다.
- 저장된 Receiver 계약은 `receiver_specs_json`에 JSON 배열로 동기화한다.

## 3D Viewer 변경

- Receiver 평면을 청색 반투명 overlay로 표시한다.
- 수광 normal을 청색 화살표로 표시한다.
- Reference 선택 중에는 Receiver/Emitter overlay가 picking을 가로막지 않도록 원본 CAD 형상을 우선한다.
- Reference vertex/edge를 선택해도 Receiver 팝업 위치가 자동으로 이동하지 않는다.

## 검증

- Python 문법 검사 통과
- 렌더링된 JavaScript `node --check` 통과
- 전체 단위 테스트 6건 통과
- 브라우저 검증
  - Datum plane 생성·편집·저장 및 JSON 계약 확인
  - Current view 자동 카메라 캡처, 3D preview 및 JSON 계약 확인
  - Reference 선택 모드 진입, vertex 선택 카운트 및 팝업 위치 고정 확인
  - 브라우저 console error 없음

## 후속 작업

- Receiver JSON을 Run simulation 요청과 직접 연결
- Receiver별 2D hit bin 및 휘도 히트맵 표시
- Reference Receiver의 component transform 자동 추종
- 원형 Receiver와 관측자 카메라 프리셋 검토
