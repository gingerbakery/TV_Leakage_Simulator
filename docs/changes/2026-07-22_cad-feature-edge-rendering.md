# CAD 면 내부 삼각선 제거 (Web UI v0.9.16)

## 증상

- `Surface + Edge` 모드에서 하나의 평평한 CAD 면이 대각선으로 나뉜 것처럼 표시됐다.
- 실제 형상 경계가 아니라 STEP 삼각화와 ROI용 adaptive subdivision 과정에서 생성된 mesh 내부선이었다.

## 원인

- 확인한 STEP 원본 형상은 88개 삼각형이지만 ROI 정밀도 확보 과정에서 532,480개 삼각형으로 세분화됐다.
- 서로 맞닿은 원본 삼각형의 세분화 깊이가 다르면 같은 선 위에 T-junction이 생긴다.
- Three.js `EdgesGeometry`는 이 선을 열린 경계로 판단해 화면에 표시했다.

## 수정

- ROI 세분화 전 CAD mesh에서 feature edge를 별도로 계산한다.
- 동일 평면의 삼각분할 대각선은 제외하고 외곽선, 실제 꺾임, 비정상 경계만 유지한다.
- Full CAD View는 이 feature edge를 사용한다.
- ROI View는 ROI 절단 경계를 보여줘야 하므로 기존 ROI mesh edge 계산을 유지한다.
- 컴포넌트 전체를 숨긴 경우 해당 컴포넌트의 feature edge도 함께 숨긴다.

## 검증

- 평면 사각형을 구성하는 두 삼각형의 내부 대각선이 제거되는 단위 테스트를 추가했다.
- 90도 꺾인 두 면의 공용 경계는 유지되는 단위 테스트를 추가했다.
- 실제 `tv_leakage_roi_left_bottom_no_gap_9.stp`는 세분화 전 CAD feature edge만 전달하도록 확인한다.
