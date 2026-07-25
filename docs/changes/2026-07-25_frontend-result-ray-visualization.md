# React Result·광선 시각화 이전

## 범위

- 완료된 ray tracing job을 React Result 사이드바와 분석 창에 연결
- Viewer에 저장된 ray path를 종류별 색상으로 표시
- Receiver direct·reflected와 Direct·Specular·Lambertian·Gaussian 표시 필터 제공
- Ray summary, Surface optical, Multi-bounce, Receiver heatmap 탭 제공

## 동작

Result 사이드바는 전체 ray 수, Receiver 도달 수·비율, 실행 시간을 표시한다.
광선 필터를 바꿔도 tracing을 다시 실행하지 않고 저장된 path overlay만 즉시
갱신하며, Receiver only·All on·All off 빠른 preset을 제공한다.

계산이 완료되면 분석 창이 자동으로 열리고 닫은 뒤에는 Result의
`분석 결과 보기`로 다시 열 수 있다. 창은 Viewer 안에서 이동과 크기 조절이
가능하며 component contribution, bounce·lobe 통계, Receiver grid를 확인한다.

## 색상 계약

- Receiver direct: green
- Receiver reflected: yellow
- Direct: blue
- Specular: orange
- Lambertian: purple
- Gaussian: cyan

광선은 모델 표면에 가려지지 않도록 별도 overlay root와 depth 비검사
LineSegments로 렌더링한다. 동일 종류의 segment를 하나의 geometry로 묶어
draw call 수가 path 수에 비례해 증가하지 않도록 했다.

## 검증

- 경로 분류·필터·segment grouping 단위 테스트
- Result 사이드바 KPI·preset 및 분석 창 탭·닫기 UI 테스트
- TypeScript typecheck, lint, 전체 Vitest, production build
- 실제 STEP CAD import 후 500-ray 실행, 분석 창 네 탭과
  `All off` 0/500 → `All on` 255/500 overlay 즉시 갱신 확인
