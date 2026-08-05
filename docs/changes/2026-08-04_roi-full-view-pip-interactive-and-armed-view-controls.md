# ROI Full View PIP 조작 + ROI 선택 중 뷰 전환/Roll

- 날짜: 2026-08-04
- 대상 브랜치: `main`

## 배경

지난 변경(`2026-08-04_roi-full-view-pip-and-fit-shortcut.md`)에서 추가한
Full View PIP는 순수 표시 전용이었다. 이번 요청은:

1. PIP도 마우스로 조작(회전/줌)할 수 있게 해달라.
2. ROI 박스 선택을 "추가"하는 중(armed 상태)에도 XY/-XY/YZ 등 축 뷰
   전환과 마우스 Roll 회전이 가능하게 해달라 — 레거시 인라인 UI
   (`run_web_legacy.py`)에는 이미 있던 동작이었다.

## 변경 사항

### PIP 마우스 조작

- `ViewerRuntime`에 `pipTarget`, `pipDistance`, `pipUserAdjusted`,
  `pipViewportRect` 추가. 렌더 루프가 매 프레임 PIP 뷰포트 사각형을
  (canvas CSS 픽셀, top-left 기준으로) 기록한다.
- PIP 영역에서 시작된 left-drag는 `orbitPipCamera()`(레거시
  `freeRotateCamera`와 동일한 yaw/pitch 쿼터니언 방식)로 `pipCamera`만
  독립적으로 회전시킨다. capture-phase `wheel` 리스너로 PIP 영역 위에서의
  휠은 `pipCamera` 줌으로 가로채 TrackballControls의 메인 카메라 줌을
  선점하지 않게 한다.
- 사용자가 PIP를 조작하면 `pipUserAdjusted = true`가 되어, 매 프레임 하던
  자동 전체-맞춤 프레이밍을 멈춘다. PIP 안에서 더블클릭하면 다시
  자동 맞춤으로 복귀한다. ROI preview가 꺼졌다가 다시 켜지면(새 ROI 진입)
  자동으로 리셋된다.

### ROI 선택(armed) 중 뷰 전환 + Roll

- `viewer-workspace.tsx`: 카메라 프리셋 버튼이 armed 상태에서도 6개 축 뷰
  (XY/-XY/YZ/-YZ/ZX/-ZX)는 활성 유지되도록 변경 (Fit/Iso는 ROI 투영
  평면이 정의되지 않으므로 계속 비활성).
- `three-viewer-canvas.tsx`: armed 중 프리셋을 바꾸면
  `runtime.roiSelectionPreset`도 함께 갱신한다 - 그렇지 않으면 새 뷰에서
  그린 박스가 이전 뷰의 평면 설정(어느 축이 무한대인지)으로 잘못
  해석된다.
- Shift/Alt+left-drag는 armed 상태에서도 box-drag 대신 `rollCamera()`
  (레거시와 동일한, view axis 기준 camera.up 회전) 동작으로 라우팅된다.
  일반 left-drag(모디파이어 없음)는 그대로 박스 선택.

## 검증

- `tsc -b` typecheck 통과.
- `vitest run` 18 files / 88 tests 통과.
