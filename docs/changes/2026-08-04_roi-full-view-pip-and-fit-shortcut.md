# ROI Full View PIP + Fit view 단축키 F

- 날짜: 2026-08-04
- 대상 브랜치: `main`

## 배경

- ROI를 활성화하면 메인 뷰가 잘라낸 ROI 형상만 보여주는데, 그 ROI가 전체
  모델에서 어느 위치인지 참고할 방법이 없었다.
- Fit view는 더블클릭으로만 가능했고, 안내 문구에 있던 "F" 단축키는
  실제로 연결되어 있지 않았다.

## 변경 사항

### ROI Full View picture-in-picture

- `frontend/src/features/viewer/three-viewer-canvas.tsx`
  - `ViewerRuntime`에 `pipCamera`(고정 앵글 원근 카메라)와
    `roiBoundsMarker`(활성 ROI의 실제 face bounding box를 감싸는 노란색
    wireframe box, `threeScene`의 최상위 자식이라 절대좌표계를 그대로
    공유) 추가.
  - ROI 활성 상태를 계산하는 effect에서 매번 `roiBoundsMarker`를
    다시 그린다 — `scope.components[].bboxMin/bboxMax`를 모아 실제 선택된
    face 범위로 박스를 만든다 (drag box의 무한 Z prism이 아니라 실제
    형상 범위를 사용).
  - 렌더 루프에서 orientation gizmo를 그린 뒤, ROI preview가 보이는
    동안 우측 하단에 scissor+viewport를 잡아 `modelRoot`(+
    `roiBoundsMarker`)를 `pipCamera`로 한 번 더 렌더링한다. 렌더 직후
    가시성 플래그를 원래대로 되돌리므로, 프레임 사이에 실행되는
    피킹/레이캐스트 로직(예: `roiPreviewRoot.visible` 분기)에는
    영향이 없다.
  - `pipCamera`는 매 프레임 `modelRoot`의 전체 bounding box에 맞춰
    고정 iso 앵글로 자동 프레이밍된다 — 메인 카메라와 독립적이라
    사용자가 메인 뷰를 조작해도 PIP는 항상 전체 모델을 보여준다.
  - `frontend/src/components/layout/viewer-workspace.tsx`에서 이
    상태(`showFullViewPip`)에 맞춰 우측 하단에 "Full View" 라벨이 붙은
    테두리(pointer-events 없음, 순수 시각적 프레임)를 표시한다.

### Fit view 단축키 F

- `three-viewer-canvas.tsx`: canvas에 `pointerenter`/`pointerleave`로
  hover 상태를 추적하고, `window`에 `keydown` 리스너를 붙여 마우스가
  뷰어 위에 있고 텍스트 입력 요소에 포커스가 없을 때 `F` 키로
  `fitCamera(runtime, 'Fit')`를 실행한다(기존 더블클릭과 동일 동작).
  Ctrl/Alt/Meta 조합은 무시해 다른 브라우저/OS 단축키와 충돌하지 않는다.
- `viewer-workspace.tsx`: Fit 버튼에 `title="Fit view (F)"` 툴팁 추가.

## 검증

- `tsc -b` typecheck 통과.
- `vitest run` 18 files / 88 tests 통과.
