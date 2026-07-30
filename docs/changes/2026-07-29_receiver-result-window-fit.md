# Receiver Heatmap 및 Result 초기 창 크기 조정

## 문제

- 확대된 Receiver Heatmap의 최대 높이 `640 px`가 Result 초기 창 높이 `560 px`보다 커서 처음 Receiver 탭을 열었을 때 하단부가 스크롤 영역 아래로 잘렸다.
- 사용자는 창 크기를 직접 조절해야 전체 Receiver와 좌표축을 한 번에 확인할 수 있었다.

## 변경 내용

### Receiver Heatmap 10% 축소

- 최대 Plot 폭: `760 px → 684 px`
- 최대 Plot 높이: `640 px → 576 px`
- 정사각형 Receiver는 최대 `576 × 576 px`로 표시된다.
- 이전 확대 전 크기보다는 충분히 크며 실제 Receiver 가로·세로 비율도 유지한다.

### Result 초기 창 확대

- 초기 창 폭: `760 px → 960 px`
- 초기 창 높이: `560 px → 880 px`
- 일반적인 FHD 이상 작업 화면에서는 Receiver 제목, KPI, Heatmap, X/Y 좌표축이 처음부터 한 창에 들어오도록 구성했다.
- 사용 가능한 Viewer 영역이 초기 크기보다 작으면 기존 경계 보호 로직이 부모 영역에 맞춰 창을 자동 축소한다.
- 사용자는 기존과 동일하게 Result 창을 드래그하거나 우측 하단 핸들로 크기를 다시 조절할 수 있다.

## 검증

- 가로형·세로형 Receiver의 10% 축소 Plot 계산 확인
- 충분한 부모 화면에서 Result 창이 `960 × 880 px`로 열리는지 확인
- 작은 부모 화면에서 Result 창 경계를 벗어나지 않는 기존 자동 축소 로직 유지
- Receiver 확대·툴팁·좌표축 기능 회귀 테스트
- Frontend 전체 테스트, lint 및 production build 수행
