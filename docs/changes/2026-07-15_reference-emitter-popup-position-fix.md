# Web UI v0.7.19 - Reference Emitter 팝업 위치 고정

## 문제

- Reference geometry emitter에서 vertex 또는 edge를 선택할 때마다 Emitter popup이 클릭 위치로 자동 이동했다.
- 사용자가 popup을 보기 편한 곳으로 옮겨도 다음 reference 선택 시 위치가 바뀌어 CAD 선택을 방해했다.

## 원인

- reference 선택 이벤트가 발생할 때마다 3D viewer의 클릭 좌표를 `showEmitterPopupAt()`에 전달하고 있었다.

## 수정

- Reference geometry 선택 중에는 현재 popup 위치를 그대로 유지한다.
- popup이 닫혀 있는 예외 상황에서만 마지막 저장 위치 또는 기본 위치로 다시 표시한다.
- CAD surface emitter의 기존 클릭 동작은 변경하지 않았다.

## 검증

- Python 문법 검사 통과
- 렌더된 JavaScript 문법 검사 통과
