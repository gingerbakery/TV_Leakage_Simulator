# Ray Tracing Add/List UI 단순화

## 목표

- Emitter와 Receiver 메뉴의 하위 구조를 더 짧고 직관적으로 정리한다.
- 선택되지 않은 생성 방식이 강조되어 보이지 않도록 버튼 색상을 통일한다.
- 별도 Cancel 버튼 없이 패널과 Properties popup으로 선택 상태를 제어한다.

## 구현 내용

- Web UI 버전을 `v0.9.2`로 갱신했다.
- Emitter와 Receiver의 하위 메뉴명을 각각 `Add`, `List`로 단순화했다.
- CAD surface, Datum plane, Reference geometry, Current view 생성 버튼을 모두 흰색으로 통일했다.
- 선택하지 않은 첫 번째 생성 방식이 파란색으로 표시되던 문제를 제거했다.
- Emitter와 Receiver의 `Cancel selection` 버튼을 삭제했다.
- 선택 중 `Add` 제목을 다시 눌러 패널을 닫으면 현재 geometry 선택을 종료한다.
- 저장 전 draft 상태에서 `Add` 패널을 닫으면 Properties popup과 draft를 함께 닫는다.
- Properties popup의 Close 버튼으로도 기존처럼 선택 상태를 종료한다.

## 검증

- 삭제된 Cancel 버튼 ID와 이벤트 참조가 남아 있지 않은지 확인했다.
- `run_web.py` Python 문법 검사를 통과했다.
- 생성 HTML 구조와 Three.js module/일반 inline JavaScript 문법 검사를 통과했다.
- 전체 단위 테스트 10개가 통과했다.
