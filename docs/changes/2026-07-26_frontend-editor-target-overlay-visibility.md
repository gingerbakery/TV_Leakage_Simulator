# Component·Emitter 선택 표시 가시성 보강

## 원인

- Material·Transform 대상은 별도 선택 geometry가 아니라 원래 surface
  material 색만 변경하고 있었다. Receiver·ROI 등 다른 overlay가 겹치거나
  대상이 앞쪽 component에 가려진 각도에서는 강조가 사라져 보일 수 있었다.
- 편집창을 열어도 Component Tree와 Viewer 하단 선택 수가 `0 component`로
  남아 실제 편집 대상과 선택 상태가 서로 모순됐다.

## 변경 내용

- Material·Transform 대상에 depth와 무관하게 보이는 반투명 주황색 면과
  선명한 edge 전용 overlay를 추가했다.
- Viewer 상단에 `Transform target · component name` 또는
  `Material target · component name` 배지를 표시한다.
- Component Tree와 Viewer 우클릭 메뉴에서 편집을 시작할 때 대상 component를
  선택 상태로 동기화하고, 숨겨진 component는 편집을 위해 다시 표시한다.
- CAD Surface Emitter는 선택 전 `click a face` 안내를 표시하고, 선택된
  면·경계·normal 화살표의 렌더 우선순위를 다른 overlay보다 높였다.

## 검증

- 실제 `tv_leakage_roi_right_bottom_no_gap.stp`에서 Transform·Material
  대상 면·edge·배지와 `1 selected` 상태를 확인했다.
- CAD Surface Emitter에서 선택 전 안내와 선택 후 4,096 triangle 면,
  경계와 normal 화살표를 확인했다.
- 브라우저 warning/error가 없으며 TypeScript, lint, Vitest와 production
  build를 검증했다.
