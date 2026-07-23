# ROI 단면 자동 채움 렌더링 (Web UI v0.9.22)

## 문제

- 기존 ROI View는 ROI와 겹치는 원본 triangle face만 남겼다.
- 원본 solid를 절단한 새로운 단면 face를 생성하지 않았기 때문에 ROI 경계에서 component가 빈 껍데기처럼 보였다.
- 큰 triangle은 ROI box와 조금만 겹쳐도 triangle 전체가 포함되어 단면 경계가 실제 ROI box보다 바깥으로 돌출될 수 있었다.

## 변경 내용

- ROI box의 `xMin/xMax/yMin/yMax`를 ROI scope에 저장하고 Three.js ROI viewer에 전달한다.
- ROI View 렌더링 시 선택된 triangle을 ROI box의 네 평면으로 정확히 clipping한다.
- clipping 후 생성된 열린 경계 중 ROI 절단 평면 위의 선분만 수집한다.
- CAD face별 tessellation에서 발생하는 T-junction을 찾아 긴 선분을 자동 분할한다.
- 인접한 ROI 절단 평면이 만나는 box corner의 끝점을 연결해 폐곡선을 완성한다.
- 폐곡선을 삼각분할하여 렌더링 전용 section cap을 생성한다.
- section cap은 본체보다 약간 진한 청색으로 표시해 실제 외피와 절단면을 구분한다.

## 렌더 모드

- `Surface + Edge`: 불투명 section cap과 cap 외곽선을 표시한다.
- `Surface`: 반투명 section cap을 표시하며 cap 외곽선은 숨긴다.
- `Wireframe`: 75% 불투명도의 어두운 section cap과 밝은 cap 외곽선을 표시한다.

## 검증

### 전체 TV 샘플 내부 ROI

- 입력 mesh: 106,352 triangle
- ROI box: `x=40~120 mm`, `y=40~120 mm`
- clipping 결과: 2,284 triangle
- section cap loop: 11개
- 열린 chain: 0개
- cap triangle: 26개

### 사용자 화면과 동일한 좌측 하단 샘플

- 파일: `_uploads/tv_leakage_roi_left_bottom_no_gap_15.stp`
- 입력 mesh: 50,944 triangle
- 검증 ROI box: `x=8~48 mm`, `y=8~48 mm`
- clipping 결과: 20,964 triangle
- section cap loop: 13개
- 열린 chain: 0개
- cap triangle: 30개

## 적용 범위와 한계

- 이번 cap은 ROI View를 보기 쉽게 만드는 렌더링 전용 geometry다.
- 원본 CAD, component transform 데이터와 ray tracing용 원본 mesh는 변경하지 않는다.
- 여러 ROI scope는 각 box의 clipped geometry를 합쳐 표시한다.
- 좌표로 단일 face를 찾는 보완 선택 방식은 volume box 정보가 없으므로 section cap을 만들지 않는다.
- 복잡한 관통홀의 inner loop를 hole로 보존하는 정밀 단면은 추후 CAD kernel boolean 또는 hole-aware triangulation으로 고도화한다.
