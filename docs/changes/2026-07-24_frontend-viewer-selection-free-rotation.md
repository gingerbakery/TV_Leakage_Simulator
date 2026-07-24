# Frontend Viewer 선택 표시·자유 회전 개선

## 증상

- Viewer에서 component를 클릭하면 선택한 triangle 위치에 노란색 표식이 남았다.
- 위·아래 극점에서 카메라 회전이 제한되어 모델을 연속으로 360도 돌릴 수 없었다.

## 원인

- picking된 face를 `depthTest: false`인 노란색 overlay로 별도 렌더링했다.
- `OrbitControls`는 고정된 up vector와 polar angle 범위로 인해 극점을
  통과하는 자유 회전을 지원하지 않는다.

## 수정

- face ID 선택 데이터는 유지하되 노란색 face overlay 렌더링을 제거했다.
- component 선택은 기존 cyan surface·edge highlight만 사용한다.
- 카메라 입력을 `TrackballControls`로 전환해 상하 극점을 통과하는
  연속 자유 회전을 지원한다.
- 정지 시 카메라의 미세 이동이 남지 않도록 damping을 사용하지 않는다.
- Viewer 크기가 바뀔 때 trackball 화면 좌표도 함께 갱신한다.

## 영향 범위

component·face ID, picking 결과와 Material·Transform 데이터는 그대로
유지한다. 선택 시각화와 카메라 조작 방식만 변경한다.

## 검증

- `npm run typecheck`
- `npm run lint`
- `npm test` — 8 files, 28 tests
- `npm run build`
- `npm audit --audit-level=high` — 취약점 0건
- Chrome에서 문제 STEP(50,944 faces, 4 components) 재검증
  - component 클릭 후 cyan highlight만 표시
  - 노란색 face overlay 없음
  - 정면·측면·바닥면과 상하 극점 너머 연속 회전
  - `Iso` preset 복귀와 선택 해제 정상
  - console error·warning 없음
