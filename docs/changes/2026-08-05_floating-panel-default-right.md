# 플로팅 패널 기본 위치를 좌측 → 우측으로 변경

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 배경

Transform editor, Material editor, Emitter/Receiver Datum Plane 등
`floating` 모드의 `AppDialog`가 열릴 때 기본적으로 뷰어 좌측에
배치되어 모델을 바로 가려버리는 문제. 위치 이동(드래그)은 유지한 채
기본 배치만 우측으로 바꿔 달라는 요청.

## 변경 사항

`frontend/src/components/common/app-dialog.tsx`의 초기 위치 계산
`useLayoutEffect`에서, x 좌표를 `viewerBounds.left + gap` (뷰어 좌측
기준)이 아니라 `viewerBounds.right - panelWidth - gap` (뷰어 우측
기준)으로 앵커링하도록 변경. `window.innerWidth - panelWidth - gap`
상한 clamp와 `floatingPanelGap` 하한 clamp는 그대로 유지.

드래그 로직(`beginDrag`, pointermove 핸들러, `wasDraggedRef`)은 전혀
건드리지 않았다 - 사용자가 한 번이라도 드래그하면 그 이후로는 항상
드래그한 위치가 우선한다(기존 동작 그대로).

## 검증

- 프런트 `tsc -b` 통과.
- `vitest run` 18 files / 94 tests 통과 - `overlays.test.tsx`의
  플로팅 다이얼로그 위치 하드코딩 값(`364px`)이 새 우측 기본값
  (`672px`)으로 바뀌어야 해서 갱신했다. 드래그 후 위치 값도 clamp
  경계(`window.innerWidth - panelWidth - 8`)에 맞춰 `676px`로 갱신.
- 실제 브라우저(Playwright + 로컬 Chrome)로 STEP 샘플을 import하고
  Step 03 Components에서 Transform editor를 열어, 다이얼로그가 뷰어
  우측 가장자리에 붙어서 열리는 것(`x=1248`, viewport 1600px 기준)과
  헤더를 드래그하면 자유롭게 이동하는 것(`x=998`로 이동)을 스크린샷
  으로 확인했다.
