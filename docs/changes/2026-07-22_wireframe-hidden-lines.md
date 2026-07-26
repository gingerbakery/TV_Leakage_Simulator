# Wireframe 75% 불투명도 및 숨은선 표시 (Web UI v0.9.21)

## 변경 배경

- Wireframe 보조 면의 불투명도를 더 낮춰 Surface 모드와 명확히 구분할 필요가 있었다.
- 기존 경계선은 깊이 검사를 사용하므로 앞쪽 부품에 가려진 객체의 선이 보이지 않았다.

## 변경 내용

- Wireframe 보조 면(`wirefill`)의 불투명도를 `75%`로 조정했다.
- Wireframe 전용 숨은선 레이어(`hiddenEdges`)를 추가했다.
- 숨은선은 낮은 밝기와 `16%` 불투명도로 표시하고, 실제로 보이는 경계선은 기존처럼 밝게 표시한다.
- 숨은선 레이어는 Wireframe에서만 활성화하며 Surface와 Surface + Edge 모드에서는 숨긴다.
- CAD feature edge만 사용하므로 adaptive mesh의 내부 삼각형 선은 숨은선에도 표시되지 않는다.

## React Viewer 이식 보완

- Full CAD와 ROI 절단 solid 모두 셰이딩 재질 대신 깊이를 기록하는 75%
  불투명도의 `MeshBasicMaterial` 면을 Wireframe 전용으로 사용한다.
- 실제 보이는 CAD feature edge는 82%, 뒤에 가려진 원본 feature edge는
  16%로 분리했다. 절단용으로 새로 생긴 section cap 경계는 숨은선
  레이어에 넣지 않아 내부 단면처럼 보이는 잘못된 형체를 방지한다.
- ROI section cap의 보이는 외곽선은 72%로 유지하고, CAD surface Emitter의
  면 강조는 Wireframe에서 16%로 낮춰 원래 모서리 판독을 방해하지 않게 했다.

## 표시 원칙

- 보이는 경계선: 밝고 선명하게 표시
- 가려진 경계선: 옅은 보조선으로 표시
- 삼각분할 내부선: 표시하지 않음
