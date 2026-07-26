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
Material·Transform 편집창은 메인 메뉴를 가리지 않도록 Viewer 왼쪽에
배치되는 이동식 패널로 열리며, 편집 중인 component는 다른 Viewer
overlay나 앞쪽 component에 가려지지 않는 주황색 면·edge overlay와
대상 이름 배지로 계속 강조한다. 편집창을 열면 Component Tree의 선택
상태와 Viewer 하단 선택 수도 같은 대상으로 동기화한다.

## 8. Three.js Viewer·선택 연동 — 완료

실제 Three.js scene과 component·face picking을 연결하고 Tree 선택·가시성,
Material·Transform, 카메라와 렌더 모드를 React 상태와 동기화했다.
중첩 component의 depth 충돌과 Wireframe 반투명 면·edge 안정화도 반영했다.
선택 지점의 잔상 없는 component highlight와 pole 제한 없는 자유 회전을 지원한다.
모델에 가려지지 않는 고정 XYZ orientation gizmo와 크기 조절도 제공한다.
카메라 프리셋은 Iso와 `±XY`, `±YZ`, `±ZX` 여섯 정면을 지원한다.
Full CAD와 잘린 ROI 표면의 component 우클릭 메뉴도 복원해 표시·해석 포함,
Material·Transform 편집과 삭제 확인으로 바로 이동할 수 있다.

## 9. ROI 선택·관리 — 완료

박스 드래그와 좌표 입력, 다중 ROI 목록, 활성 범위 계산 및 Viewer
highlight를 기존 동작과 같은 데이터 계약으로 이전한다. ROI 경계로 잘린
solid는 폐곡선 section cap으로 채우며 열린 chain이나 빈 껍데기를
허용하지 않는다.

박스 ROI는 원본 triangle을 XY·YZ·ZX 중 선택한 평면의 네 경계에서 정밀
clipping해 새 교차 vertex를 생성하고, component별 폐곡선 loop를
삼각분할한 section cap과 외곽선을 추가한다. 활성 ROI가 있으면 Full CAD
대신 닫힌 ROI solid만 표시하며, 새 ROI를 추가하는 동안에는 전체 모델을
다시 열어 다른 범위를 선택한다. ROI 선택 중에만 가장 가까운 여섯 정면으로
정렬하고 완료 후에는 위치·target·up·near/far를 포함한 선택 전 카메라
화면을 그대로 복원한다. 절단 surface의 평면 셰이딩과 Wireframe 전용
재질·깊이 범위로 회전 중 경계 물결, 면 노이즈와 깜빡임을 방지한다.
ROI 절단 surface와 section cap에도 component 식별 정보를 유지해 Viewer
직접 선택, Tree 선택, Material·Transform 대상 강조가 같은 화면에 표시된다.
원본 face가 없는 section cap은 component 선택만 허용하고 CAD surface
Emitter 면으로는 지정하지 않는다.
활성 component Transform은 source face/component ID를 바꾸지 않는다.
원본 좌표에서 ROI surface·section cap·feature edge를 먼저 완성한 뒤,
Viewer와 Python 계산 계층이 공유하는 component bounding-box 중심 기준
move·tilt 행렬을 절단 solid 전체에 적용한다. 이동 후 기존 ROI 박스로
재절단하지 않으므로 section cap의 열린 경계와 지그재그 회귀를 방지한다.
활성 ROI가 있으면 Component Tree와 Transform·Material 편집기는 ROI에
참여한 component와 해당 ROI face 수·면적을 기준으로 대상을 표시한다.

## 10. Emitter·Receiver·Ray tracing 실행 — 완료

광원과 수광부 배치, 실행 옵션, 비동기 job 진행률을 React UI와 Python
계산 API에 연결했다. CAD surface·Datum plane Emitter와 Datum plane·현재
카메라 기준 Receiver를 같은 `EmitterSpec`·`ReceiverSpec` 계약으로 관리하고
Viewer에 발광면·수광면과 normal 방향을 표시한다.

Material assignment, component Transform, 해석 제외 component와 활성 ROI를
`RayTraceRequest`로 조립하며 `/api/raytrace/start`와 300 ms polling으로
queued·preparing·tracing·completed·failed 상태, ray 수, 경과·잔여 시간을
표시한다. 설정이 바뀌면 이전 결과 job을 무효화하고 다시 계산하도록 했다.
ROI → component X +1.5 mm Transform → part Material → CAD surface Emitter
→ Current View Receiver → ray tracing의 전체 계약을 실제 CAD와 Python API
양쪽에서 회귀 검증했다.

CAD surface 선택창은 Viewer 조작을 막지 않는 플로팅 패널로 동작한다. 한 번
클릭하면 연결된 동일 평면 patch를 선택하며, 선택 중인 발광면은 주황색
채움·경계선·normal 화살표와 triangle 수 배지로 명확히 구분한다. 선택
전에도 Viewer에 면 클릭 안내 배지를 표시하고, 선택 overlay는 다른 설정
overlay보다 높은 순서로 렌더링한다.
저장 후에는 Full CAD와 ROI 절단 화면 모두에 노란 발광면·외곽선·방향
화살표를 유지한다. Receiver 기준면은 component와 구별되는 보라색으로
표시하며, Emitter·Receiver 공통 방향 표시는 완전히 불투명한 얇은 선과
작은 열린 V자 촉만 사용해 모델을 가리지 않고 방향만 보여준다.
Datum Emitter와 Datum·Current View Receiver 설정도 Viewer 왼쪽에서
시작하는 같은 이동식 비모달 패널을 사용한다. 좌표·회전·크기를 바꾸는
동안 기준면과 방향을 Viewer에 실시간 미리보기로 표시한다. Current View
Receiver는 30 × 30 mm와 모델 중심 기준 30 mm 거리를 기본값으로 사용해
수광면이 카메라 근접면에서 화면 전체를 덮지 않게 한다.

생성된 Emitter·Receiver 목록에는 설정 편집 버튼을 제공하며, 생성에 사용한
같은 비모달 패널을 기존 값으로 다시 열어 ID를 유지한 채 ray 수, 출력,
좌표·회전, 크기, 해상도와 수광각을 수정한다. Current View Receiver는 기존
카메라 프레임을 유지해 열리며 필요할 때만 `Use current camera`로 갱신한다.
Viewer의 CAD 발광면·가상 발광면·수광면을 우클릭하면 설정 편집,
활성화/비활성화와 삭제 메뉴를 바로 사용할 수 있다.

## 11. Result·광선 시각화 — 완료

완료된 ray tracing job의 핵심 지표와 component contribution을 Result
사이드바와 이동·크기 조절 가능한 분석 창으로 이전했다. Surface optical,
Multi-bounce, Receiver heatmap도 탭별로 확인할 수 있다.

저장된 광선 경로는 Viewer 위에 Direct·Specular·Lambertian·Gaussian 및
Receiver direct·reflected 색상으로 표시한다. 여섯 표시 필터와 빠른 preset은
재계산 없이 overlay만 갱신하며 현재 표시 경로 수를 함께 보여준다.

## 12. Python API 서버 분리 — 예정

`run_web.py`에 섞여 있는 HTTP·UI 책임을 FastAPI 계층으로 옮기고 계산
모듈은 현재 Python 코어를 그대로 재사용한다.

## 13. 데스크톱 패키징·최종 전환 — 예정

React production build와 Python API를 WebView2 실행기에 통합하고 회귀
검증 후 기존 인라인 UI를 대체한다.
