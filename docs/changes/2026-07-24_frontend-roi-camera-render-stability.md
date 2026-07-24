# Frontend ROI camera and render stability

## 증상

- ROI 박스 선택 후 카메라가 정면 시점에 남거나 `Fit` 과정에서 up vector가
  시선과 평행해져, 결과 각도가 뒤집히고 회전이 고정된 것처럼 보였다.
- 절단 geometry에 smooth normal과 넓은 카메라 depth 범위를 사용해
  면·모서리 경계에 물결 무늬가 생기고 Wireframe 회전 중 노이즈가 보였다.

## 수정

- ROI 선택 직전의 정규화된 시선 방향과 up vector를 저장한다.
- 선택 중에만 가까운 XY·-XY 정면으로 정렬하고, 완료·취소·실패 후 저장한
  방향으로 복원한 다음 결과 bounding box 크기에 맞춰 거리만 조절한다.
- `Fit`은 기존 up vector를 유지해 TrackballControls의 자유 회전을
  방해하지 않는다.
- 절단 surface·section cap을 flat shading으로 렌더링한다.
- Wireframe의 반투명 면은 조명 계산이 없는 `MeshBasicMaterial`과
  depth write를 사용하고, CAD feature edge와 cap 외곽선만 겹쳐 그린다.
- 카메라 near/far를 `distance / 1000`과 `distance * 20` 기준으로 좁혀
  ROI Fit 이후의 depth buffer 정밀도를 안정화한다.

## 검증

- 실제 `tv_leakage_roi_left_bottom_no_gap.stp`
  - 50,944 faces, 4 components
  - 임의 각도 → ROI XY 선택 → 기존 각도 복원 확인
  - 선택 직후 연속 Trackball 회전 확인
  - 23,967 source faces → 24,863 clipped triangles
  - 10개 section cap, 열린 경계 없이 닫힌 solid 생성
  - Surface, Surface + Edge, Wireframe에서 경계 물결·깜빡임·면 노이즈 없음
- TypeScript typecheck, lint, 35개 단위 테스트 통과
- 최신 브라우저 console error·warning 없음
