# Receiver Heatmap 확대 및 광량 툴팁

## 목적

- Receiver 결과에서 미소 영역의 광분포를 확대해 확인할 수 있도록 한다.
- 마우스 위치의 Receiver 로컬 좌표와 해당 셀에 누적된 광량을 즉시 확인할 수 있도록 한다.

## 구현 내용

### 마우스 휠 확대·축소

- Heatmap 위에서 마우스 휠을 위로 움직이면 확대되고 아래로 움직이면 축소된다.
- 확대 중심은 Heatmap 중앙이 아니라 현재 마우스 커서가 가리키는 위치로 유지된다.
- 확대 범위에 맞춰 하단 X축과 우측 Y축의 좌표 범위 및 눈금이 자동 갱신된다.
- `Reset view` 버튼 또는 Heatmap 더블클릭으로 전체 Receiver 보기로 복귀한다.
- 최대 확대 배율은 Receiver grid 해상도를 기준으로 정하며 최대 128배로 제한한다.

### 좌표·광량 툴팁

- Heatmap 위에 마우스 커서를 올리면 다음 값을 표시한다.
  - Receiver 로컬 X, Y 좌표 `(mm)`
  - Receiver grid 셀 번호
  - 해당 셀의 누적 입사광속 `Incident flux (lm)`
  - 셀 면적으로 나눈 광속 밀도 `Flux density (lm/mm²)`
  - 광속 밀도를 환산한 조도 `Illuminance (lx)`
- Y 좌표와 grid row는 백엔드 좌표계의 `+Y`가 화면 위쪽으로 보이도록 변환한다.

## 광량 데이터 의미

- 툴팁의 `Incident flux`는 단일 ray 하나의 power가 아니다.
- Ray tracing 중 해당 Receiver grid 셀에 도달한 direct ray와 reflected ray의 수광 power를 합산한 `flux_lumen` 값이다.
- `Illuminance`는 `flux_lumen / bin_area_m²`로 계산한 셀 단위 추정값이다.
- 현재 grid보다 작은 영역으로 화면을 확대할 수는 있지만, 새로운 해석 해상도가 생성되는 것은 아니다. 더 미세한 물리 분포가 필요하면 Receiver의 X/Y resolution을 높인 뒤 Ray tracing을 다시 실행해야 한다.

## 변경 파일

- `frontend/src/features/results/receiver-heatmap.ts`
- `frontend/src/features/results/result-window.tsx`
- `frontend/src/features/results/receiver-heatmap.test.ts`
- `frontend/src/features/results/result-ui.test.tsx`

## 검증

- 커서 기준 확대 후 동일한 물리 좌표가 커서 아래에 유지되는지 확인
- 확대 범위에 따라 좌표축 눈금이 다시 계산되는지 확인
- 화면 상단의 `+Y` grid 셀과 백엔드 `flux_lumen` row가 올바르게 매핑되는지 확인
- 좌표, 누적 광속, 광속 밀도 및 조도 툴팁 출력 확인
- Frontend 전체 테스트, TypeScript typecheck, lint 및 production build 수행
