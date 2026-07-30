# Receiver Tooltip 클리핑 및 Plot 크기 개선

## 문제

- Receiver Heatmap의 확대 영역을 자르기 위해 적용한 `overflow-hidden`이 좌표·광량 Tooltip까지 함께 잘라냈다.
- Receiver 결과 목록이 넓은 화면에서 2열로 배치되어 Receiver가 1개뿐인 경우에도 Heatmap 폭이 절반 수준으로 축소됐다.
- 기존 최대 Plot 크기 `460 × 320 px`는 미소 광분포를 확인하기에 작았다.

## 변경 내용

### Tooltip 레이어 분리

- Receiver Heatmap을 다음 두 레이어로 분리했다.
  - `Heatmap viewport`: 확대된 canvas만 경계 안에서 자르는 레이어
  - `Tooltip overlay`: viewport 바깥에 배치되어 Receiver 경계를 넘어가도 잘리지 않는 레이어
- Tooltip은 기존과 동일하게 커서 위치에 따라 좌우·상하 방향을 자동 전환한다.

### Heatmap 크기 확대

- Receiver 결과를 항상 1열로 배치해 분석 창의 가로 폭을 최대한 사용한다.
- 최대 Plot 크기를 다음과 같이 확대했다.
  - 최대 폭: `460 px → 760 px`
  - 최대 높이: `320 px → 640 px`
- 정사각형 Receiver는 최대 `640 × 640 px`로 표시되며 기존 대비 면적이 크게 증가한다.
- Receiver의 실제 가로·세로 물리 비율은 그대로 유지한다.

## 검증

- Tooltip이 Heatmap viewport의 자식이 아닌 overlay 형제 레이어인지 확인
- Heatmap viewport만 `overflow-hidden`을 유지하는지 확인
- 휠 확대, 좌표축 갱신, 광량 Tooltip 및 Reset 기능 회귀 테스트
- 가로형·세로형 Receiver의 최대 Plot 폭 계산 확인
- Frontend 전체 테스트, lint 및 production build 수행
