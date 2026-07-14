# Three.js Viewer 전환 계획

## 목적
- 현재 Canvas 2D 기반 viewer를 Three.js 기반 3D viewer로 단계적으로 교체한다.
- CAD import, ROI 선택, component 선택, transform preview, ray path overlay의 조작성을 개선한다.
- 백엔드 ray tracing 구조는 유지하고 viewer 계층만 먼저 고도화한다.

## 현재 판단
- 전체 프레임워크 전환은 아직 이르다.
- 하지만 3D viewer는 ray tracing 테스트 효율에 직접 영향을 주므로 먼저 교체 가치가 크다.
- 현재 단계에서는 `FastAPI/React/Next.js` 전환 없이 `run_web.py`의 viewer 영역만 Three.js로 교체하는 것이 가장 현실적이다.

## 적용 원칙
- 백엔드 scene payload는 `docs/viewer-data-contract.md`의 `mesh-scene.v1`을 기준으로 한다.
- ray tracing, CAD import, ROI backend는 그대로 유지한다.
- 기존 Canvas viewer는 안정화 기간 동안 fallback으로 유지할 수 있다.
- Three.js dependency는 사내/회사 PC 시연을 고려해 CDN보다 local vendor 방식이 우선이다.

## 권장 dependency 방식

### 우선안: local vendor
- 예시 위치:
  - `web/static/vendor/three.module.min.js`
  - `web/static/vendor/OrbitControls.js`
- 장점:
  - 회사 PC offline/보안망에서도 동작
  - EXE/WebView 패키징에 포함 가능
- 단점:
  - vendor 파일 버전 관리 필요

### 대안: npm frontend
- 예:
  - `frontend/`
  - Vite + React + TypeScript + Three.js 또는 React Three Fiber
- 장점:
  - 장기 구조에 적합
  - Zustand, Tailwind, shadcn/ui와 연결 쉬움
- 단점:
  - Node/npm 환경 추가
  - 현재보다 packaging 복잡도 증가

### 비권장: CDN only
- 회사 PC에서 인터넷/보안망 이슈가 생길 수 있다.
- 빠른 데모에는 쓸 수 있지만 안정 시연용 기본값으로는 부적합하다.

## 단계별 전환

### Phase 1: 데이터 계약 고정
- 완료 기준:
  - `schema_version`
  - `mesh.vertices`
  - `mesh.faces`
  - `mesh.face_ids`
  - `mesh.face_component_ids`
  - `components`
  - `metadata`
- 관련 문서:
  - `docs/viewer-data-contract.md`

### Phase 2: Three.js read-only viewer
- 목표:
  - CAD mesh를 Three.js `BufferGeometry`로 표시
  - orbit/pan/zoom
  - fit/front/top/right/reverse camera preset
  - surface / edge / surface+edge mode
- 이 단계에서는 ROI 선택 로직을 아직 옮기지 않는다.
- 2026-07-14 적용:
  - `run_web.py`에 Three.js 기반 read-only viewer를 추가했다.
  - `web/static/vendor/`에 local vendor 방식으로 Three.js와 OrbitControls를 포함했다.
  - 기존 Canvas viewer는 fallback으로 유지하고, UI에서 `Three.js` / `Canvas`를 전환할 수 있게 했다.
  - 현재 Three.js viewer는 표시/카메라 조작 중심이며, face/component picking은 다음 Phase에서 연결한다.
- 2026-07-14 추가 적용:
  - Canvas 전환 UI를 숨기고 Three.js viewer를 기본 viewer로 고정했다.
  - XYZ axis triad 시인성을 개선했다.
  - camera preset 적용 시 첫 클릭부터 position/up/target이 고정되도록 안정화했다.

### Phase 3: Picking 연결
- 목표:
  - face picking
  - component picking
  - hover highlight
  - selected face/component highlight
- 기준:
  - face 선택은 `face_id`
  - component 선택은 `component_id`

### Phase 4: ROI / Transform / Material 연결
- 목표:
  - ROI highlight
  - transform preview overlay
  - applied transform overlay
  - material assignment highlight
- 2026-07-14 부분 적용:
  - Three.js transform preview/applied overlay를 추가했다.
  - Apply된 component의 원래 face를 기본 mesh에서 숨기고, 이동/tilt된 위치를 빨간 overlay로 표시한다.
  - ROI highlight, material assignment highlight는 아직 기존 로직과 분리되어 있으며 다음 단계에서 이관한다.

### Phase 5: Ray result overlay
- 목표:
  - ray path line overlay
  - receiver hit map
  - leak intensity heatmap
  - before/after delta overlay

## Three.js geometry 생성 규칙
- `mesh.vertices`를 flat `Float32Array`로 변환한다.
- `mesh.faces`를 flat index array로 변환한다.
- triangle 순서는 `mesh.faces` 순서를 유지한다.
- Three.js raycaster의 triangle index는 `face_id`로 환산 가능해야 한다.

## 선택 상태 규칙
- ROI:
  - `Set<face_id>`
- component selection:
  - `Set<component_id>`
- local face move:
  - `Set<face_id>`
- transform target:
  - `component_id` 또는 `face_id[]`
- material target:
  - `component_id` 또는 `face_id[]`

## 안정화 체크리스트
- 샘플 CAD가 비어 있지 않게 렌더링됨
- STEP import mesh가 Three.js에 표시됨
- 기존 camera preset이 동작함
- component tree 클릭과 viewer highlight가 일치함
- face picking 결과가 기존 `face_id`와 일치함
- ROI 선택이 기존 form 값과 동기화됨
- transform preview가 기존 summary와 일치함
- EXE/WebView 패키지에서도 local vendor 파일이 로드됨

## 전환 중 주의사항
- Three.js viewer가 도입되어도 ray tracing 좌표계는 backend model 좌표를 유지한다.
- 화면 표시를 위한 좌표 변환은 viewer 내부에서만 처리한다.
- ID 계약을 깨는 CAD import 변경은 반드시 `docs/viewer-data-contract.md`를 함께 갱신한다.

## 다음 구현 후보
1. Three.js raycaster 기반 face picking 연결
2. component picking과 component tree 선택 상태 동기화
3. ROI highlight / transform preview / material assignment overlay를 Three.js로 이관
4. Canvas fallback 유지 범위와 제거 시점 결정
5. ray path / heatmap / before-after overlay를 Three.js layer로 확장
