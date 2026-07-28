# STEP component 실이름·CAD 색상 반영

- 날짜: 2026-07-28
- 대상 브랜치: `main`

## 배경

기존 OCP STEP import(`_import_step_ocp`)는 `STEPControl_Reader`(형상 전용
리더)만 사용했다. 이 리더는 STEP 파일의 product 구조(XCAF)를 읽지 않기
때문에, NX 등에서 Export한 실제 "Component Name"과 body 색상을 알 수 없고,
모든 solid에 `STEP Solid {N}` 형태의 순번 이름만 부여되고 있었다.

## 변경 사항

- `src/leakage_simulator/importers.py`
  - `_ensure_ocp_xcaf_available()` 추가: `STEPCAFControl_Reader`,
    `XCAFDoc_DocumentTool`(ShapeTool/ColorTool), `TDataStd_Name`,
    `Quantity_Color` 등 XCAF 관련 OCP 모듈을 지연 import.
  - `_read_step_named_colored_solids()` 추가: STEP의 assembly/product 트리를
    (accumulated `TopLoc_Location` 합성으로) 재귀 순회하여, 각 solid의
    실제 컴포넌트 이름과 표시 색상(`Quantity_Color` → `#rrggbb`)을
    글로벌 좌표계 solid와 함께 추출한다. 인스턴스 레벨 이름/색상이 있으면
    우선하고, 없으면 정의(prototype) 레벨 값으로 폴백한다.
  - `_import_step_ocp()`: XCAF 추출이 성공하면 그 solid 목록을 그대로 사용
    (solid별로 개별 `BRepMesh_IncrementalMesh` 수행 후 face 탐색). 실패/빈
    결과일 때만 기존 `STEPControl_Reader` + 순번 이름 경로로 폴백한다.
  - face metadata에 `step_component_color`(`#rrggbb` 또는 없음) 추가.
- `src/leakage_simulator/components.py`
  - `build_face_groups`/`_build_group_items`가 `step_component_color`를
    집계해 각 component dict에 `color` 필드(`str | None`)를 추가.
- `roi.py`의 `build_scene_payload`는 `objects`/`components`를 그대로
  전달하므로 별도 수정 없이 `color` 필드가 API 응답에 포함된다.
- Frontend
  - `frontend/src/api/types/scene.ts`: `SceneComponent.color: string | null`
    추가.
  - `frontend/src/features/viewer/three-viewer-canvas.tsx`: CAD 색상이
    있으면 우선 사용하고 없으면 기존 palette로 폴백하는
    `resolveComponentColor()` 추가, 표면 재질·ROI 프리뷰·선택 하이라이트
    3곳의 palette 직접 참조를 이 함수 호출로 교체.
  - `frontend/src/features/components/component-tree-panel.tsx`: component
    아이콘에 CAD 색상 스와치(작은 dot) 표시.
  - `frontend/src/test/scene-fixture.ts`: 새 필수 필드 `color: null` 추가.

## 검증

- `samples/tv_leakage_full_assembled_no_gap.stp` (4-solid 조립품)에서
  `Chassis_Rear`, `LCD_Cell_3T`, `Frame_Middle_FMB`, `Cover_Deco` 실제
  이름과 각 body 색상(hex)이 정확히 추출됨을 직접 확인.
  (이전에는 전부 `STEP Solid 1..4`로만 표시됨.)
- 실행 중인 FastAPI(`/api/scene?cad=...`)에서도 동일하게 이름·색상 확인.
- Python 회귀 테스트 86개 통과.
- Frontend `tsc -b` typecheck, `vitest run`(12 files / 60 tests) 통과.

## 알려진 제한

- product 구조가 없는(오래된 STEP 등) 파일은 기존처럼 순번 이름/색상 없음
  경로로 자동 폴백한다.
- cadquery 기반 폴백 경로(`_import_step`, OCP 자체가 없을 때만 사용)는
  이번 변경 대상에서 제외했다 — 이미 컴포넌트 분리 없이 단일 mesh로
  가져오는 별도 제한이 있던 경로라 범위 밖으로 유지.
