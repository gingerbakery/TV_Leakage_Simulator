# Frontend ROI selection and closed solid clipping

## 문제

- React ROI 초안은 박스와 교차한 원본 face 전체를 노란 overlay로
  표시했다.
- ROI 외 영역이 그대로 남았고, face 단위 경계가 adaptive triangle
  크기를 따라 톱니처럼 보였다.
- 실제 교차 vertex와 section cap이 없어 격리 뷰를 만들면 빈 껍데기가
  될 수 있었다.

## 수정

- 기존 `run_web.py`에서 검증된 ROI clipping·section cap 알고리즘을
  TypeScript/Three.js 모듈로 이식했다.
- Sutherland-Hodgman 방식으로 원본 triangle을 `xMin`, `xMax`,
  `yMin`, `yMax` 평면에서 순서대로 절단한다.
- 절단 교차점은 component·좌표 기준으로 병합해 곧은 경계와 새로운
  vertex를 생성한다.
- 절단 경계 edge를 component·box·plane별로 모으고 T-junction을
  분할한 뒤, 열린 chain의 box 모서리 구간을 연결한다.
- 폐곡선만 `ShapeUtils.triangulateShape`로 삼각분할해 불투명 section
  cap을 만들고 cap 외곽선을 별도 geometry로 생성한다.
- 열린 chain이 하나라도 남으면 격리 mesh를 표시하지 않고 무결성
  오류를 보고한다.
- 활성 박스 ROI가 있으면 Full CAD를 숨기고 clipped surface·section
  cap·CAD feature edge·cap edge만 표시한다.
- 새 ROI 추가를 무장하면 전체 CAD를 다시 표시해 기존 ROI 밖도
  선택할 수 있게 한다.
- 좌표 입력, 다중 scope 활성화, Hide/Delete component 제외와
  `face_id[]` 분석 계약은 유지한다.
- ROI 박스 선택 전에 카메라의 시선 방향과 up vector를 저장하고,
  선택 완료·취소·무결성 실패 시 같은 방향으로 복원한 뒤 결과 크기만
  `Fit`한다.
- `Fit`은 현재 up vector를 덮어쓰지 않으며 XY·-XY·Iso 프리셋만
  각 프리셋의 안정적인 up vector를 설정한다.
- 절단 surface와 cap은 flat shading을 사용하고, Wireframe 면은
  조명과 무관한 `MeshBasicMaterial`로 분리했다.
- 카메라 near/far 비율을 기존 검증 범위로 축소해 절단 후 작은 모델에서
  발생하던 depth 정밀도 저하와 z-fighting을 제거했다.

## 검증

- 폐쇄 cube 절단 회귀 테스트
  - `x=0.25`, `x=0.75`에 새 경계 vertex 생성
  - section cap loop 2개
  - 열린 chain 0개
- 실제 TV STEP: 50,944 faces · 4 components
  - 활성 ROI 입력: 22,720 source faces
  - 정밀 clipping 결과: 23,344 triangles
  - section cap: 7개
  - 열린 chain 없이 ROI solid만 표시
  - Surface + Edge와 Wireframe에서 직선 경계와 채워진 절단면 확인
- ROI scope 비활성화 시 Full CAD 복귀, 재활성화 시 격리 ROI 복원
- 실제 TV STEP 재선택 회귀:
  - 임의 회전 시점에서 ROI 선택 중 XY 정렬 후 원래 시선 방향 복원
  - 선택 직후 Trackball 자유 회전 정상
  - 23,967 source faces → 24,863 clipped triangles
  - section cap 10개, Surface·Surface + Edge·Wireframe 경계 안정
- 브라우저 console error·warning 없음
- `npm run typecheck`
- `npm run lint`
- `npm test` — 9 files, 35 tests
