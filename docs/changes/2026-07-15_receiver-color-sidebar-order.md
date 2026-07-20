# Web UI v0.8.1 - Receiver 색상 및 메뉴 순서 개선

## 변경 목적

- Receiver overlay와 component 선택 highlight가 모두 파란색으로 표시되어 혼동되는 문제를 해결한다.
- 핵심 작업 메뉴와 정보·관리 메뉴를 분리해 실제 작업 순서를 더 명확하게 만든다.

## Receiver 색상 변경

- Receiver 3D 평면, 외곽선, 수광 normal 화살표를 청색 계열에서 보라색 계열로 변경했다.
- component 선택 highlight는 기존 파란색을 유지한다.
- Receiver properties 팝업, 생성 버튼, Receiver 목록 표시도 같은 보라색 계열로 통일했다.
- Emitter는 노란색/주황색, Receiver는 보라색, component 선택은 파란색으로 구분한다.

## 메뉴 순서 변경

- 세로형과 가로형 메뉴 모두 다음 순서를 사용한다.
  1. ROI 설정
  2. Components
  3. Ray tracing
  4. Result
  5. Transform manager
  6. Material library
- Ray tracing과 Result는 각각 Step 4, Step 5로 변경했다.
- Transform manager와 Material library는 작업 단계가 아닌 정보·관리 메뉴이므로 `Reference`로 표시한다.
- 메뉴 기능과 내부 데이터 연결은 변경하지 않고 표시 순서만 조정했다.

## 검증 항목

- 세로형 accordion 표시 순서
- 가로형 tab 표시 순서
- Receiver와 component highlight 색상 구분
- 기존 Receiver 생성·편집 기능 유지
