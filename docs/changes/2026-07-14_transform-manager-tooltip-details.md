# 2026-07-14 Transform manager tooltip/details 정리

## 배경
- Transform manager의 설명 문구와 회색 요약 박스가 기본 화면에 계속 노출되어 Component/Transform 작업 흐름을 복잡하게 보이게 했다.
- Components 메뉴와 동일하게, 설명은 `?` tooltip로 숨기고 상세 상태는 접이식으로 확인하는 방식이 더 적합하다고 판단했다.

## 변경 사항
- Web UI version을 `v0.7.5`로 올렸다.
- `Transform Manager` 제목 옆에 `?` help icon을 추가했다.
- 기존 제목 아래 파란색 설명 문구를 help popover로 이동했다.
- `Active:`, `Checked rules`, `대상:` 등 상태 요약을 `Information` 접이식 메뉴 안으로 이동했다.
- `Information`을 닫은 기본 상태에서는 transform rule list와 주요 조작 영역만 보이도록 정리했다.

## 검증
- Python compile 통과
- inline JavaScript syntax check 통과
- Helical Gear STP import 후 Transform manager UI 확인
  - help icon 표시 확인
  - `Information` 기본 닫힘 확인
  - `Active:` 및 `대상:` 텍스트가 접이식 메뉴 내부에 유지됨 확인

## 2026-07-14 추가 수정 (`v0.7.6`)
- `Details` 명칭을 `Information`으로 변경했다.
- Transform manager의 `Information` 접이식 메뉴를 `Advanced` 바로 위로 이동했다.
- 별도 테두리 스타일을 제거하고 기존 `Advanced`와 같은 기본 접이식 형태로 맞췄다.
- Component 메뉴의 회색 선택 요약 박스도 `Information` 접이식 메뉴 안으로 이동했다.
