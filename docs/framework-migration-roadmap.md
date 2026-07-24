# 프레임워크 전환 로드맵

기존 `run_web.py` 화면과 계산 동작을 유지하면서 React 프론트엔드와 분리된
Python API 구조로 단계적으로 전환한다.

## 1. 기준 커밋 생성 — 완료

CAD, ROI, Viewer 관련 미커밋 변경을 검증하고 전환 전 복구 기준이 되는
커밋을 생성했다.

## 2. 전환 브랜치 생성 — 완료

기존 개발 흐름과 분리된 `codex/framework-migration` 브랜치를 만들고
원격 저장소에 연결했다.

## 3. Vite + React + TypeScript 기반 생성 — 완료

`frontend/`에 독립 개발·빌드가 가능한 차세대 프론트엔드 작업 공간과
기본 검사 명령을 구성했다.

## 4. Tailwind CSS + shadcn/ui + 디자인 토큰 — 완료

공통 UI 컴포넌트와 dark theme, Viewer·선택·광선 상태를 표현하는
시뮬레이터 전용 색상 토큰을 정의했다.

## 5. API 타입·fetch·상태 계층 — 완료

Python API 계약과 공통 fetch client를 TypeScript로 정의하고, 서버 상태는
TanStack Query, 작업 상태는 Zustand가 관리하도록 경계를 분리했다.

## 6. 레이아웃 셸·Dialog·Context Menu — 완료

Workflow sidebar와 Viewer workspace를 React 레이아웃으로 옮기고,
기능 화면이 공유할 Dialog·확인창·우클릭 메뉴 기반을 마련했다.

## 7. Component Tree·Material·Transform — 완료

실제 `ScenePayload.components`를 Tree에 연결하고 선택·표시·해석 상태,
Material assignment와 Transform rule 편집 흐름을 React로 이전했다.

## 8. Three.js Viewer·선택 연동 — 완료

실제 Three.js scene과 component·face picking을 연결하고 Tree 선택·가시성,
Material·Transform, 카메라와 렌더 모드를 React 상태와 동기화했다.
중첩 component의 depth 충돌과 Wireframe 반투명 면·edge 안정화도 반영했다.
선택 지점의 잔상 없는 component highlight와 pole 제한 없는 자유 회전을 지원한다.
모델에 가려지지 않는 고정 XYZ orientation gizmo와 크기 조절도 제공한다.

## 9. ROI 선택·관리 — 완료

박스 드래그와 좌표 입력, 다중 ROI 목록, 활성 범위 계산 및 Viewer
highlight를 기존 동작과 같은 데이터 계약으로 이전한다. ROI 경계로 잘린
solid는 폐곡선 section cap으로 채우며 열린 chain이나 빈 껍데기를
허용하지 않는다.

박스 ROI는 원본 triangle을 XY 경계에서 정밀 clipping해 새 교차 vertex를
생성하고, component별 폐곡선 loop를 삼각분할한 section cap과 외곽선을
추가한다. 활성 ROI가 있으면 Full CAD 대신 닫힌 ROI solid만 표시하며,
새 ROI를 추가하는 동안에는 전체 모델을 다시 열어 다른 범위를 선택한다.
ROI 선택 중에만 정면 XY로 정렬하고 완료 후에는 선택 전 시선 방향을
복원한다. 절단 surface의 평면 셰이딩과 Wireframe 전용 재질·깊이 범위로
회전 중 경계 물결, 면 노이즈와 깜빡임을 방지한다.

## 10. Emitter·Receiver·Ray tracing 실행 — 예정

광원과 수광부 배치, 실행 옵션, 비동기 job 진행률을 React UI와 Python
계산 API에 연결한다.

## 11. Result·광선 시각화 — 예정

Direct·reflected ray와 component contribution 결과를 Viewer overlay,
요약 지표 및 결과 창으로 이전한다.

## 12. Python API 서버 분리 — 예정

`run_web.py`에 섞여 있는 HTTP·UI 책임을 FastAPI 계층으로 옮기고 계산
모듈은 현재 Python 코어를 그대로 재사용한다.

## 13. 데스크톱 패키징·최종 전환 — 예정

React production build와 Python API를 WebView2 실행기에 통합하고 회귀
검증 후 기존 인라인 UI를 대체한다.
