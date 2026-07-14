# 2026-07-14 Component/Material information UI 정리

## 배경
- Component와 Material library 메뉴의 회색 요약 박스가 기본 화면에서 계속 노출되어 tree/list 조작 흐름을 방해했다.
- Transform manager와 동일하게 설명은 `?` tooltip로, 상태 요약은 `Information` 접이식 메뉴로 통일한다.

## 변경 사항
- Web UI version을 `v0.7.7`로 올렸다.
- Component 메뉴의 `Information` 접이식 메뉴를 Component Tree 아래로 이동했다.
- Material Library 제목 옆에 `?` help icon을 추가했다.
- Material Library 설명 문구를 help popover로 이동했다.
- Material 대상/적용 요약 회색 박스를 `Information` 접이식 메뉴 안으로 이동했다.

## 검증
- Python compile
- inline JavaScript syntax check
- HTML 생성 시 `Component tree help`, `Material library help`, `Information` 접이식 메뉴 존재 확인

## 2026-07-14 추가 수정 (`v0.7.8`)
- Component 메뉴의 `Information`을 제목 바로 아래로 다시 이동했다.
- Transform manager의 `Information`과 `Advanced`를 제목 바로 아래로 이동했다.
- Material library의 하위 tree 항목들을 기본 닫힘 상태로 변경했다.
  - `Base materials`
  - `Surface properties`
  - `BSDF assets`
