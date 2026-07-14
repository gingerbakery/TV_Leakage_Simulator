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

## 2026-07-14 추가 수정 (`v0.7.9`)
- ROI 설정 메뉴가 초기 화면에서 자동으로 열리지 않도록 변경했다.
- 세로형 메뉴에서 모든 메뉴가 닫힌 상태도 허용하도록 sidebar open 상태 보정 로직을 제거했다.
- 가로형 메뉴 전환 시에는 기존처럼 `ROI 설정`을 기준 탭으로 사용할 수 있도록 active tab 값은 유지했다.

## 2026-07-14 추가 수정 (`v0.7.10`)
- 3D viewer 하단에 따로 노출되던 `World coordinates` 정보를 viewer 내부 상단 overlay로 이동했다.
- 기본 표시를 `Center / Size` 한 줄 요약으로 바꾸고, 상세 `Origin / Center / BBox`는 클릭해서 펼쳐보는 방식으로 정리했다.
- 큰 모델이나 긴 페이지에서도 좌표 정보를 보기 위해 스크롤을 많이 내려야 하는 문제를 줄였다.

## 2026-07-14 추가 수정 (`v0.7.11`)
- 좌측 메뉴 panel과 우측 3D viewer의 스크롤을 완전히 분리했다.
- 전체 page/body 스크롤을 막고, 좌측 panel만 독립적으로 세로 스크롤되도록 변경했다.
- 우측 3D viewer는 viewport 높이에 고정되어 좌측 메뉴를 많이 펼쳐도 모델 view가 아래로 밀리지 않도록 했다.
