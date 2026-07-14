# 2026-07-14 Three.js UI cleanup before commit

## 배경
- Three.js viewer와 transform overlay가 동작하기 시작하면서, 초기 디버깅용 UI 요소들이 실제 사용 흐름에서 시야를 방해했다.
- Component 메뉴 설명은 항상 노출하기보다 필요한 순간에만 확인할 수 있는 tooltip 방식이 더 적합하다고 판단했다.

## 변경 사항
- 3D viewer 상단의 `Face`, `Vertex`, `Mode` KPI 카드 영역을 숨겼다.
- 기존 오른쪽 상단 `Transform preview` 고정 패널을 숨겼다.
- Transform 상세 정보는 오른쪽 `Transform input` popup 내부의 `Preview / applied details` 접이식 메뉴로 이동했다.
- Component 메뉴의 설명 문장을 기본 화면에서 숨기고, `Assembly / Component Tree` 제목 옆 `?` tooltip로 옮겼다.
- Web UI version을 `v0.7.4`로 올렸다.

## UX 기준
- 기본 화면은 CAD model, component tree, transform popup 중심으로 단순화한다.
- 설명은 항상 보이게 하지 않고, 제목 옆 `?` hover/focus 설명으로 제공한다.
- 실무자가 바로 조작해야 하는 버튼과 tree를 우선 노출한다.

## 검증
- HTML 생성 및 JavaScript syntax check
- Helical Gear STP import 후 KPI/Transform preview 고정 패널 미표시 확인
- Component title tooltip 표시 확인
- Transform popup 내부에 `Preview / applied details` 접이식 상세가 표시됨을 확인
