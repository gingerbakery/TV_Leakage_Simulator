# Frontend 레이아웃·Dialog·Context Menu 이전

## 목적

기존 `run_web.py`의 좌측 workflow sidebar와 우측 3D Viewer 구조를 React
App Shell로 먼저 옮기고, Component·Material·Transform 기능이 공통으로
사용할 overlay 기반을 구성한다.

## 레이아웃 매핑

### Legacy

- 460px 좌측 설정 panel
- Model import와 세로형 workflow accordion
- ROI, Components, Transform manager, Material, Ray tracing, Result
- 우측 Viewer toolbar, KPI, Full/ROI viewport
- Component 우클릭 메뉴와 개별 popup

### React

- desktop 352px workflow sidebar + 유동형 Viewer workspace
- mobile 2열 workflow navigation + 세로형 Viewer flow
- 상단 application header와 API layer 준비 상태 영역
- Model import card와 기능별 navigation boundary
- Camera, render mode, KPI, status bar를 포함한 Viewer shell
- Viewer engine이 들어갈 독립 viewport slot
- `SimulatorShell`, `WorkflowSidebar`, `ViewerWorkspace`를 분리해 후속 기능
  이전이 각 영역 안에서 독립적으로 진행되도록 구성

## 공통 overlay

- shadcn/ui Radix 기반 `Dialog`, `ContextMenu`, `ScrollArea` 추가
- 공통 크기·header·footer를 제공하는 `AppDialog`
- destructive action을 위한 `ConfirmationDialog`
- legacy 메뉴 순서를 유지한 `ComponentContextMenu`
  - Hide / Show
  - Traceability Off / On
  - Material
  - Transform
  - Delete…
- Context Menu에서 연 Dialog가 닫힐 때 원래 CAD 대상에 focus 복귀
- Escape 닫기, focus trap, 배경 interaction 차단
- 메뉴·workflow button의 명시적 accessible name

## 현재 preview 동작

- workflow navigation 전환
- Camera preset과 render mode 선택
- 우클릭 Component visibility/traceability 상태 전환
- Material·Transform 안내 Dialog
- Delete 확인 Dialog

preview 상태는 공통 UI 검증 전용이며 Python API나 legacy 상태를 변경하지
않는다.

## 다음 단계 경계

- Component Tree가 실제 `ScenePayload.components`를 렌더링
- Context Menu action을 Zustand component 상태에 연결
- Material assignment와 Transform editor를 공통 Dialog에 연결
- 실제 Three.js Viewer는 현재 viewport slot에 후속 연결

## 검증

- `npm run typecheck`
- `npm run lint`
- `npm test`
- `npm run build`
- `npm audit --audit-level=high`
- 공통 overlay 및 focus 복귀 테스트
- 1280×720 desktop과 390×844 mobile 브라우저 렌더링
- 실제 right-click → Material Dialog → Escape focus 복귀
- Delete confirmation action과 status 반영
- console warning/error 및 horizontal overflow 확인
