# ROI 적용 후 Viewer 선택 호환성 검증 보고서

- 검증일: 2026-07-26
- 대상 브랜치: `codex/framework-migration`
- 확인 주소: `http://127.0.0.1:5173/`
- 검증 모델: `samples/tv_leakage_roi_right_bottom_no_gap.stp`

## 현상과 원인

활성 ROI가 있으면 원본 CAD scene root는 숨겨지고 절단 결과를 담은 별도
ROI preview root만 화면에 표시된다. 기존 선택 상태와 highlight는 숨겨진
원본 CAD root에만 반영되어, 내부적으로 component가 선택되어도 사용자는
Viewer에서 아무 변화가 없는 것처럼 보였다.

또한 ROI 경계에서 새로 생성한 section cap에는 원본 face ID와 component
ID가 없어서, cap을 클릭했을 때 어떤 component인지 역추적할 수 없었다.

## 수정 내용

- ROI 절단 surface의 모든 triangle에 원본 face ID와 component ID를 함께
  유지한다.
- component별 section cap triangle에도 component ID를 기록한다.
- ROI preview 위에 전용 선택 overlay를 그려 Component Tree와 Viewer 직접
  선택 결과를 같은 노란색 강조로 표시한다.
- 선택 surface와 edge는 depth buffer를 따르도록 해 앞쪽의 다른 component를
  투과하지 않으며, surface 강조를 옅게 조정해 원래 면 셰이딩과 모서리를
  함께 읽을 수 있게 한다.
- Transform·Material 편집 대상과 CAD surface Emitter 선택 면도 ROI 화면
  위에서 별도 강조한다.
- component ID별 move·tilt 행렬을 원본 component 중심에 적용한 뒤 ROI를
  다시 절단한다. source face ID와 component ID는 변환 전후 동일하게
  유지해 Tree·Transform·Material·Ray tracing 계약이 어긋나지 않게 한다.
- 원본 CAD face가 아닌 section cap 클릭은 component 선택으로 처리한다.
  Emitter 면 선택 중에는 cap을 발광면으로 잘못 등록하지 않고 안내 문구를
  표시한다.
- Viewer 상단에 현재 선택된 component 이름 배지를 표시해 상태를 이중으로
  확인할 수 있게 했다.

## 실제 브라우저 검증

| 시나리오 | 결과 |
| --- | --- |
| Full CAD에서 Viewer 직접 component/face 선택 | 통과 |
| ROI 절단 surface 직접 선택 및 노란 강조 | 통과 |
| 앞쪽 component에 가려진 선택 surface·edge의 depth 차폐 | 통과 |
| ROI section cap 클릭으로 component 선택 | 통과 |
| Component Tree 단일·다중 선택과 ROI 강조 동기화 | 통과 |
| ROI 상태에서 Transform 대상 강조 | 통과 |
| ROI 상태에서 component move·tilt 적용·비활성화·재활성화 | 통과 |
| ROI 상태에서 Material 대상 강조 | 통과 |
| 원본 CAD face를 Emitter 발광면으로 선택 | 통과 |
| ROI section cap의 Emitter 면 오등록 차단 | 통과 |
| `±XY`, `±YZ`, `±ZX` 여섯 방향에서 ROI 선택 | 통과 |
| Surface·Wireframe 렌더 모드에서 선택 유지 | 통과 |
| 새 탭에서 CAD Import 후 ROI 생성·선택 | 통과 |
| 새 탭 콘솔 warning/error | 0건 |

새 탭 검증에서는 50,944-face CAD를 Import한 뒤 ROI 32,768 faces,
33,414 clipped triangles, 8 section caps가 생성된 상태에서 직접 선택,
Tree, Transform, Material을 순서대로 확인했다.

후속 회귀 검증에서는 `STEP Solid 4`에 `X +20 mm`, `Rz +8°`를 적용했다.
변환 전 ROI는 33,414 triangles, 적용 후에는 변환된 좌표로 다시 절단되어
33,053 triangles가 되었다. rule 비활성화 시 33,414, 재활성화 시
33,053으로 되돌아와 component ID와 Transform rule 연결을 확인했다.

## 자동 검증

- TypeScript typecheck
- oxlint
- Vitest 전체 테스트
- Vite production build

위 검증은 이 변경의 로컬 커밋 직전에 모두 통과한 상태로 기록한다.
