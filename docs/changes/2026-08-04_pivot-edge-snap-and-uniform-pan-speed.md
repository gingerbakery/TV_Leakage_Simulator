# Pivot pick의 edge 끝점/중간점 스냅 + Pan 속도 통일

- 날짜: 2026-08-04
- 대상 브랜치: `main`

## Pan 속도 통일

Full view와 ROI view의 pan 속도가 다르게 튜닝되어 있던 걸(`0.3` /
`0.06`) 요청에 따라 둘 다 `0.3`으로 통일했다. ROI 상태별로 동적으로
바꿔주던 코드(및 그 근거였던 주석)를 제거하고, `controls.panSpeed`를
생성 시점에 한 번만 `0.3`으로 설정한다.

## NX 스타일 Edge 끝점/중간점 스냅

배경: 뷰어에서 pivot을 클릭으로 찍을 때 지금까지는 raycast가 맞은 面
위의 정확한 좌표를 그대로 썼다. 실제로 회전 기준점으로 쓰는 지점은
거의 항상 edge 끝점(모서리/교차점)이거나 edge 중간점이라, NX 등
CAD 툴처럼 그 두 종류를 우선 스냅해야 한다는 요청.

- `three-viewer-canvas.tsx`에 `resolveEdgePivotSnap()` 추가:
  - 클릭된 componentId에 속한 `scene.mesh.feature_edge_segments`만
    검사 (전체 모델이 아니라 클릭한 부품의 edge만).
  - 각 segment의 시작점/끝점(priority 0)과 중간점(priority 1)을 현재
    카메라로 화면에 투영해, 클릭 좌표와의 픽셀 거리를 계산.
  - 22px 이내 후보 중 우선순위가 더 높은(끝점 우선) 것을, 동순위면 더
    가까운 것을 선택.
  - 22px 이내에 아무 후보도 없으면 기존처럼 raycast 표면 좌표를
    그대로 사용 (동작 변화 없음).
  - 새 백엔드 데이터는 필요 없음 - `feature_edge_segments`는 이미
    STEP import 시 계산되어 scene payload에 포함되어 있었다.
- 스냅되면 상태 메시지가 `Pivot picking · edge 지점에 스냅 · (x, y,
  z)`로, 아니면 기존과 동일하게 `Pivot picking · (x, y, z) 선택됨`으로
  표시된다.

## 검증

- `tsc -b`, `vitest run` 18 files / 91 tests 통과 (기존 스위트 - 스냅
  로직 자체는 실제 브라우저로 시각 검증).
- 실제 브라우저(Playwright + Chrome)로 확인:
  - 모델 모서리 근처를 클릭하니 픽셀 단위 오차 없이 정확한 CAD 코너
    좌표(800, 450, 45)로 스냅되고 상태 메시지에도 "edge 지점에 스냅"
    표시됨 - 스냅 안 됐다면 임의의 소수점 좌표가 나왔을 것.
  - 모델 표면 위 60개 지점을 격자로 스캔해서, edge 끝점(반복되는 정확한
    좌표)과 edge 중간점(예: 코너 X=800의 정확히 절반인 X=400) 모두
    실제로 스냅되는 것을 다수 확인했다.
