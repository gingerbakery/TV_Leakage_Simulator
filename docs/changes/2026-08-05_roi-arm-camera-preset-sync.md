# ROI 박스 선택 arm 시 카메라 프리셋 표시가 실제 뷰와 어긋나던 문제 수정

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 배경

"ROI 추가 후 드래그"를 armed 상태로 만들면, 뷰어는 현재 카메라 방향에서
가장 가까운 직교 축 뷰(XY/-XY/YZ/-YZ/ZX/-ZX)로 자동 스냅된다
([[roi-orthographic-snap]] 참고). 그런데 이 자동 스냅은 Three.js
카메라를 `runtime` 레벨에서 직접 옮길 뿐, 툴바 프리셋 버튼과 뷰어
좌상단 pill이 읽는 React/Zustand `cameraPreset` 상태는 건드리지
않았다. 그 결과, 예를 들어 "Iso" 상태에서 바로 arm하면 실제 카메라는
YZ로 스냅되는데 버튼/필은 계속 "Iso"로 표시되어, 사용자가 "XY로
설정해놨는데 ROI 누르면 계속 다른 화면(YZ)으로 간다"처럼 원인을 알 수
없는 상태로 보였다. 실제로는 라벨만 낡은 것이고 뷰 자체의 자동 스냅
로직은 정상 동작하고 있었다(직접 재현·검증함).

이 모델(TV 패널)은 X/Y에 비해 Z가 아주 얇아서, YZ로 스냅되면 화면에
거의 실선처럼 보이는 매우 비실용적인 엣지뷰가 나온다 - 그래서 "낡은
라벨" 버그가 유독 눈에 띄고 혼란스러웠다.

## 변경 사항

- `frontend/src/features/viewer/three-viewer-canvas.tsx`:
  `ThreeViewerCanvasProps`에 `onCameraPresetChange?(preset)` 콜백을
  추가. ROI arm 이펙트에서 `nearestRoiCameraPreset`으로 실제 스냅된
  프리셋을 계산해 `fitCamera`를 호출하는 바로 그 지점에서 이 콜백도
  같이 호출해, 부모에게 "실제로 지금 이 프리셋으로 가 있다"고
  보고한다.
- `frontend/src/components/layout/viewer-workspace.tsx`: 이
  콜백을 기존 `cameraPreset` state의 setter(`setCameraPreset`)에
  그대로 연결. 이제 arm 시 자동 스냅된 프리셋이 툴바 버튼의 pressed
  상태와 pill 텍스트에 정확히 반영된다.

버튼을 눌러 프리셋을 수동으로 바꾸는 기존 경로(`setCameraPreset` +
`setCameraRequestId` 증가)는 그대로 두었다 - 이번 콜백은 "자동 스냅"
경로에서만 발생하는 라벨 desync를 메꾸는 것이라 `cameraRequestId`는
건드리지 않는다(다시 `fitCamera`를 트리거할 필요가 없음).

## 검증

- 프런트 `tsc -b` 통과. `vitest run` 18 files / 94 tests 통과(영향
  없음 - 순수 콜백 배선 추가).
- 실제 브라우저(Playwright + 로컬 Chrome)로 STEP 샘플 import 후,
  아무 프리셋도 안 건드린 기본(Iso) 상태에서 바로 "+ ROI 추가 후
  드래그"를 눌러 확인:
  - 수정 전: `YZ` 버튼은 안 눌린 상태, `Iso` 버튼이 계속 pressed로
    남아있음 (실제 뷰는 YZ로 스냅되어 있는데도).
  - 수정 후: `YZ` 버튼이 `aria-pressed="true"`로 정확히 표시되고
    `Iso`는 `false` - 상태바 텍스트("ROI 박스 선택 · YZ view · ...")
    와 완전히 일치.
