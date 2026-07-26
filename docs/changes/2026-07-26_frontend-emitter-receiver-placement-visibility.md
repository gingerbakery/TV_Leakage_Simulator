# Emitter 면 선택·Current View Receiver 가시성 개선

## 문제

- CAD surface Emitter용 면을 클릭해도 선택 상태와 방향을 즉시 알아보기
  어려웠다.
- 활성 ROI에서는 Full CAD component root가 숨겨지므로, 선택 중에는
  보이던 CAD surface Emitter가 저장 직후 함께 사라졌다.
- Current View Receiver의 크기는 30 × 30 mm가 맞지만 기본 View distance
  100 mm가 현재 모델의 카메라 거리와 거의 같아, 수광면이 카메라 근접면에서
  화면 전체를 덮는 것처럼 투영됐다.
- Receiver가 component와 비슷한 청록색이라 기준면을 구분하기 어려웠고,
  Emitter·Receiver normal 화살촉이 작고 가늘었다.

## 변경 내용

- 선택 중인 Emitter 면을 주황색 채움과 외곽선으로 강조하고, 면 중심에
  normal 방향 화살표를 표시한다.
- 저장된 CAD surface Emitter는 노란색 발광면·외곽선·방향 화살표를
  영구 표시한다. ROI가 활성화된 경우 source face/component ID와 현재
  Transform을 사용해 절단 발광면을 다시 생성한다.
- Viewer 상태 배지에 `Emitter surface · N triangles`를 표시해 연결된
  동일 평면 patch가 몇 개 triangle으로 선택됐는지 확인할 수 있게 했다.
- Receiver 기준면은 component와 겹치지 않는 보라색으로 변경하고 저장
  상태의 투명도도 높였다.
- 공통 방향 화살표의 한 픽셀 선을 원통형 shaft로 교체하고, cone 화살촉의
  길이·폭·분할 수를 늘려 어느 카메라 각도에서도 방향을 읽기 쉽게 했다.
- Current View Receiver의 기본 크기를 30 × 30 mm로 명시적으로 유지하고,
  패널을 새로 열 때마다 해당 크기로 초기화한다.
- 기본 View distance를 30 mm로 조정하고 거리 기준을 설정창에 안내한다.

## 검증

- 실제 `tv_leakage_roi_right_bottom_no_gap.stp`(50,944 faces, 4 components)
  Import 후 Emitter 면 채움·경계선·normal 화살표·triangle 수를 확인했다.
- ROI 32,768 faces 상태에서 4,096-face CAD surface Emitter를 저장한 뒤
  선택 상태가 해제되어도 노란 발광면과 방향 화살표가 유지되는지 확인했다.
- Current View Receiver 미리보기와 생성 결과가 30 × 30 mm이고 모델을
  확인할 수 있는 보라색 기준면으로 표시되는지 확인했다.
- Emitter·Receiver의 활성화 토글 전후에도 저장된 배치와 표시가 정상
  복원되는지 확인했다.
- 브라우저 warning/error가 없음을 확인하고 TypeScript, lint, Vitest,
  production build를 검증했다.
