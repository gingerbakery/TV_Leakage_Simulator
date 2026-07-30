# Receiver Heatmap 실제 크기 비율 및 좌표계 표시

## 문제

- Receiver 결과 Heatmap이 고정된 가로 직사각형으로 표시되어 사용자가 설정한 Receiver 폭과 높이 비율을 반영하지 못했다.
- Heatmap에 좌표 기준이 없어 밝은 영역이 Receiver 중심에서 어느 방향과 거리에 있는지 해석하기 어려웠다.

## 변경

- RayTraceResult에 포함된 Receiver의 `width_mm`, `height_mm`를 Heatmap 표시 비율에 직접 연결했다.
- `30 × 30 mm` Receiver는 정사각형, `60 × 30 mm` Receiver는 2:1 직사각형으로 표시된다.
- 최대 표시 폭과 높이 안에서 실제 종횡비를 유지하여 Result 창에 맞게 자동 축소한다.
- Receiver 중심을 로컬 좌표 `(0,0)`으로 정의했다.
- 화면 오른쪽을 `+X`, 왼쪽을 `-X`, 위쪽을 `+Y`, 아래쪽을 `-Y`로 표시한다.
- X/Y 중앙축, 중심점, 전체 좌표 범위를 mm 단위로 표시한다.
- Backend Grid의 row는 `-Y → +Y` 순서로 저장되므로 화면 출력 시 행을 반전해 `+Y`가 위쪽에 오도록 정렬했다.

## 좌표 정의

- Heatmap X축은 Receiver의 로컬 `u_axis` 방향이다.
- Heatmap Y축은 Receiver의 로컬 `v_axis` 방향이다.
- 좌표 범위는 `X = -width/2 ~ +width/2`, `Y = -height/2 ~ +height/2`다.

## 검증

- 가로형·세로형 Receiver의 실제 종횡비 계산 테스트
- Backend Grid의 Y축 화면 방향 변환 테스트
- Result UI의 aspect ratio, 중심 좌표, X/Y 범위 표시 테스트
