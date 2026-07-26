# Emitter 면 선택·Current View Receiver 가시성 개선

## 문제

- CAD surface Emitter용 면을 클릭해도 선택 상태와 방향을 즉시 알아보기
  어려웠다.
- Current View Receiver의 크기는 30 × 30 mm가 맞지만 기본 View distance
  100 mm가 현재 모델의 카메라 거리와 거의 같아, 수광면이 카메라 근접면에서
  화면 전체를 덮는 것처럼 투영됐다.

## 변경 내용

- 선택 중인 Emitter 면을 주황색 채움과 외곽선으로 강조하고, 면 중심에
  normal 방향 화살표를 표시한다.
- Viewer 상태 배지에 `Emitter surface · N triangles`를 표시해 연결된
  동일 평면 patch가 몇 개 triangle으로 선택됐는지 확인할 수 있게 했다.
- Current View Receiver의 기본 크기를 30 × 30 mm로 명시적으로 유지하고,
  패널을 새로 열 때마다 해당 크기로 초기화한다.
- 기본 View distance를 30 mm로 조정하고 거리 기준을 설정창에 안내한다.

## 검증

- 실제 `tv_leakage_roi_right_bottom_no_gap.stp`(50,944 faces, 4 components)
  Import 후 Emitter 면 채움·경계선·normal 화살표·triangle 수를 확인했다.
- Current View Receiver 미리보기와 생성 결과가 30 × 30 mm이고 모델을
  확인할 수 있는 크기로 표시되는지 확인했다.
- 브라우저 warning/error가 없음을 확인하고 TypeScript, lint, Vitest,
  production build를 검증했다.
