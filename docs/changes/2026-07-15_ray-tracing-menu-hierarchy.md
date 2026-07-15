# Ray tracing 메뉴 계층 및 기본 닫힘 상태 통일

## 목적

- Ray tracing 메뉴를 Components, Transform Manager, Material Library와 같은 UI 규칙으로 통일한다.
- Emitter와 Receiver를 Ray tracing의 명확한 하위 항목으로 분리한다.
- 최상위 메뉴와 모든 하위 메뉴가 닫힌 상태로 시작하도록 초기 상태를 고정한다.

## Web UI 버전

- `v0.7.17`

## UI 구조 변경

Ray tracing 메뉴 내부 순서를 아래와 같이 변경했다.

1. 제목 `Ray tracing` 및 도움말 아이콘
2. `Information`
3. `Advanced`
4. `Emitter`
5. `Receiver`
6. 비활성화된 Run simulation 버튼

### Information

- Emitter와 Receiver의 역할을 간단히 설명한다.
- 기본 상태는 닫힘이다.

### Advanced

- 전역 ray tracing 계산 조건을 이동했다.
  - Ray count
  - Max depth
  - Output folder
  - Seed
  - `k_abs`
  - `k_brdf`
- 기본 상태는 닫힘이다.

### Emitter

- 기존 CAD식 face emitter 생성 UI를 하위 드롭다운으로 이동했다.
- Emitter 목록, 면 선택, hidden `EmitterSpec` payload 기능은 유지한다.
- 기본 상태는 닫힘이다.

### Receiver

- Receiver 전용 하위 드롭다운을 추가했다.
- 현재는 다음 개발 단계임을 표시하는 placeholder 상태다.
- 기본 상태는 닫힘이다.

## 전체 메뉴 기본 상태

- `activeSideTab` 초기값을 `null`로 변경했다.
- 세로형 기본 UI에서 열린 최상위 메뉴가 없도록 했다.
- Result를 포함한 모든 최상위 메뉴는 닫힌 상태로 시작한다.
- `setResultMessage()`가 상태 메시지를 기록할 때 Result 메뉴를 자동으로 여는 동작을 제거했다.
- 실제 결과를 의도적으로 보여줄 때만 `setResultMessage(text, { openResult: true })`를 사용할 수 있다.
- 모든 `<details>` 하위 메뉴는 `open` 속성 없이 생성한다.

## 검증

- Python 문법 검사 통과
- 렌더링된 module/classic JavaScript `node --check` 통과
- 브라우저 초기 상태 확인
  - 열린 최상위 메뉴 0개
  - Result 닫힘
  - 열린 `<details>` 0개
- Ray tracing 메뉴를 연 뒤 하위 상태 확인
  - Information 닫힘
  - Advanced 닫힘
  - Emitter 닫힘
  - Receiver 닫힘
- 브라우저 console error 없음

