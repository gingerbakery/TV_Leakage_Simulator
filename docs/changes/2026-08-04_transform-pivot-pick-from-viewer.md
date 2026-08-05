# Tilt pivot를 뷰어에서 직접 클릭으로 지정

- 날짜: 2026-08-04
- 대상 브랜치: `main`

## 배경

직전 변경(`2026-08-04_transform-custom-tilt-pivot.md`)에서 Custom pivot을
X/Y/Z 숫자 입력으로만 지정할 수 있게 했는데, 좌표를 직접 타이핑하는 게
직관적이지 않다는 피드백을 받았다. 3D 모델을 보면서 원하는 지점을 바로
클릭해서 지정할 수 있어야 한다.

## 변경 사항

### 상태 (workspace-store.ts)

- `pivotPickArmed: boolean`, `pivotPickPoint: Vector3Value | null` 추가
  (프로젝트 저장 대상이 아닌 휘발성 UI 상태 - `roiBoxSelectionArmed`/
  `emitterFaceSelectionArmed`와 동일한 성격).
- Transform editor는 뷰어에 대한 직접 참조가 없으므로, 뷰어가 집어낸
  점을 dialog가 가져가는 유일한 통로가 이 store 필드다.

### Viewer (three-viewer-canvas.tsx)

- `resolveSurfaceHit()`가 이제 hit point(`hit.point`, world 좌표)도
  함께 반환.
- `pivotPickArmed`가 켜져 있으면(store에서 직접 구독, `emitterFaceSelectionArmed`와
  동일한 패턴) 클릭 시 평소의 component/face 선택 대신 그 지점을
  `actions.setPivotPickPoint(...)`로 기록하고 즉시 해제(disarm)한다.
  표면을 못 맞히면 다시 클릭하라는 안내만 띄우고 armed 상태를 유지한다.
- armed 중에는 canvas 커서를 crosshair로 표시 (기존 ROI/Emitter 패턴과
  동일).

### Transform editor dialog

- "Tilt pivot" 섹션의 Custom point 모드에 **"뷰어에서 좌표 선택"** 버튼
  추가. 누르면 picking을 arm하고 버튼 라벨이 "뷰어에서 표면을
  클릭하세요…"로 바뀐다.
- Dialog는 `floating`(non-modal) panel이라 열려 있는 상태에서 뷰어를
  그대로 조작할 수 있다 - 별도로 닫을 필요 없음.
- `pivotPickPoint`가 store에 채워지면(그리고 dialog가 열려 있으면) 한
  번만 소비해서 pivot 필드에 반영하고 즉시 store에서 비운다 - 나중에
  dialog를 다시 열었을 때 오래된 pick이 재사용되는 걸 막는다.
- Dialog가 닫히면(picking 도중이라도) armed 상태를 강제로 해제한다 -
  안 그러면 뷰어가 crosshair 상태로 멈춰있는데 결과를 받을 dialog가
  없는 상태가 될 수 있다.
- Pivot 입력 필드는 여전히 남아있다 - 클릭으로 대략 지정한 뒤 숫자로
  미세 조정하는 것도 가능하다.

## 검증

- `tsc -b` 통과, `vitest run` 18 files / 91 tests 통과 (pick 왕복
  플로우 + dialog 닫힘 시 disarm 테스트 추가).
- 실제 브라우저(Playwright + Chrome)로 CAD import → Transform editor →
  Custom point → "뷰어에서 좌표 선택" → 모델 클릭까지 직접 확인.
  클릭한 지점의 실제 3D 좌표(예: 500.18, 232.22, 33)가 정확히 Pivot
  X/Y/Z 입력에 채워지는 것을 스크린샷으로 확인했다.
