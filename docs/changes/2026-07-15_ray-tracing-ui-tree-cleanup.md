# Ray Tracing UI 트리 구조 정리

## 목표

- Ray Tracing 메뉴에서 생성 기능과 등록 객체 관리 기능을 분리한다.
- Emitter와 Receiver를 동일한 조작 구조로 맞춘다.
- 정보성 항목과 고급 설정을 메뉴 하단으로 이동하고 색상 체계를 단순화한다.

## 구현 내용

- Web UI 버전을 `v0.9.1`로 갱신했다.
- Emitter 메뉴를 `Add emitter`와 `Emitter tree`로 분리했다.
- `Add emitter`에는 CAD surface, Datum plane, Reference geometry 생성 방식만 배치했다.
- `Emitter tree`에는 등록된 광원별 `Settings`와 `Delete` 버튼을 배치했다.
- Receiver 메뉴를 `Add receiver`와 `Receiver tree`로 동일하게 분리했다.
- `Receiver tree`에도 등록된 수광면별 `Settings`와 `Delete` 버튼을 배치했다.
- 목록 행 전체 클릭 동작을 제거해 설정 진입과 삭제 동작을 명확히 구분했다.
- Components, Transform Manager, Material Library, Ray Tracing의 `Information`과 `Advanced`를 각 메뉴 최하단에 배치했다.
- Emitter/Receiver 생성 버튼, 선택 상태, 목록과 popup의 주조색을 파란색·회색 계열로 통일했다.
- 모든 상위 메뉴와 Ray Tracing 하위 메뉴는 닫힌 상태로 시작한다.

## 검증

- `run_web.py` Python 문법 검사를 통과했다.
- 생성 HTML의 주요 DOM ID가 각각 한 번만 존재하는지 확인했다.
- Three.js module script와 일반 inline script를 분리해 Node 문법 검사를 통과했다.
- 전체 단위 테스트 10개가 통과했다.

## 후속 작업

- 실제 사용자 화면에서 Tree 행 폭과 popup 위치를 확인하고 필요 시 반응형 간격을 미세 조정한다.
- Ray Tracing 계산 조건은 RT-2 구현 범위가 확정된 뒤 `Advanced` 안에서 재정리한다.
