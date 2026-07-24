# React Viewer component 우클릭 메뉴 복원

## 변경 내용

- Full CAD와 ROI clipped surface를 우클릭하면 원본 `component_id`를 찾아
  `Hide/Show`, `Traceability Off/On`, `Material`, `Transform`, `Delete…`
  메뉴를 포인터 위치에 표시한다.
- 우클릭은 component만 선택하고 face 강조는 남기지 않는다.
- 우클릭 드래그는 카메라 pan으로 유지하며, 빈 공간·ROI 박스 선택·Emitter
  surface picking 중에는 component 메뉴를 열지 않는다.
- component 메뉴가 열린 동안의 휠 입력은 Three.js canvas로 전달해,
  메뉴를 닫지 않고 계속 확대·축소할 수 있다.
- Material·Transform은 기존 공통 편집 Dialog로 연결하고 Delete는 기존
  확인창을 거친다.

## ROI 매핑

ROI clipping으로 새로 생성된 triangle은 geometry의 `sourceFaceIds`를
보존한다. Viewer 우클릭은 이 ID를 `face_component_ids`에 다시 매핑하므로,
잘린 표면에서도 원본 component 작업을 동일하게 수행한다.

## 검증

- 실제 STEP 샘플 50,944 faces / 4 components import
- Full CAD와 ROI isolated solid에서 우클릭 메뉴 표시 확인
- Traceability Off/On, Hide/Show 상태 전환 확인
- Material·Transform Dialog와 Delete 확인창 연결 확인
- 메뉴가 열린 상태의 휠 확대·축소와 메뉴 유지 확인
- TypeScript typecheck, oxlint, Vitest 통과
