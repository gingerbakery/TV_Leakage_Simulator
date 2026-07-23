# ROI View 삼각형 mesh 선 제거 (Web UI v0.9.23)

## 문제

- ROI clipping 후 새로 생성한 surface에 Three.js `EdgesGeometry`를 적용했다.
- CAD face의 triangle winding과 clipping triangle 경계에 따라 내부 삼각분할선이 feature edge로 오판되어 Wireframe에서 대각선 패턴이 다시 표시됐다.

## 변경 내용

- ROI surface에서 `EdgesGeometry` 기반 edge 재계산을 제거했다.
- STEP import 시 보존한 원본 CAD feature edge만 ROI box에 맞춰 line clipping하여 표시한다.
- section cap은 triangulation 결과에서 edge를 다시 계산하지 않고, cap 생성에 사용한 폐곡선 자체를 외곽선 geometry로 사용한다.
- 보이는 선은 아래 두 종류로 제한한다.
  - ROI 내부의 원본 CAD feature edge
  - ROI 절단면 section cap의 실제 외곽선
- adaptive subdivision edge와 cap 내부 triangulation edge는 표시하지 않는다.

## 검증

- 파일: `_uploads/tv_leakage_roi_left_bottom_no_gap_15.stp`
- ROI box: `x=8~48 mm`, `y=8~48 mm`
- clipped surface: 20,964 triangle
- cap loop: 13개
- open chain: 0개
- cap 외곽선: 56개 segment
- ROI 내부 CAD feature edge: 31개 segment
- triangle mesh edge: 0개

## 영향 범위

- ROI View의 Wireframe 및 Surface + Edge 선 표현만 변경한다.
- Full CAD View의 기존 CAD feature edge 렌더링은 유지한다.
- Surface geometry, section cap, ROI 선택 결과와 ray tracing 데이터는 변경하지 않는다.
