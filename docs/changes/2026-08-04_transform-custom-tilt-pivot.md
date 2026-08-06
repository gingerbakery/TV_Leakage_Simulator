# Transform editor: 커스텀 Tilt pivot

- 날짜: 2026-08-04
- 대상 브랜치: `main`

## 배경

기존 Transform editor의 Tilt(Rx/Ry/Rz)는 항상 대상 component/local face의
bounding box 중심을 회전 피벗으로 사용했다. 사용자가 회전 기준점을 직접
지정하고 싶다는 요청이 있었다 (예: 힌지처럼 실제 회전축이 부품 중심이
아닌 경우).

## 변경 사항

### 데이터 모델

- `frontend/src/stores/workspace-store.ts`: `ComponentTransformRule`에
  `pivot?: Vector3Value | null` 추가. `null`/미지정 = 기존과 동일하게
  bounding box 중심.
- `frontend/src/features/projects/bitsam-project.ts`: 프로젝트 저장/불러오기
  검증에 `pivot` optional 필드 반영.

### UI (`transform-editor-dialog.tsx`)

- Tilt 아래에 "Tilt pivot" 섹션 추가: "Component center"(기본) /
  "Custom point" 토글.
- Custom point 선택 시 X/Y/Z(mm) 입력 노출, 처음 전환하면 현재 component의
  bounding box 중심 좌표로 미리 채워서 그 지점부터 조정할 수 있게 했다.
- Pivot 입력 필드의 접근성 이름은 `Pivot x/y/z`로 별도 지정 - Move의
  `x/y/z`와 라벨이 겹치는 문제를 피했다.
- Reset 버튼도 pivot을 center 모드로 되돌리도록 확장.

### Viewer (`three-viewer-canvas.tsx`)

- Component 지오메트리는 로컬 원점이 자기 bounding box 중심에 오도록
  베이크되어 있어, 그룹은 항상 "자기 원점" 기준으로만 회전한다. 임의의
  피벗 P를 지원하기 위해 `pivotAdjustedPosition()`을 추가했다:
  `position = P + move + R*(center - P)` (P가 center와 같으면 기존 수식
  `center + move`로 정확히 환원됨).
- `applyComponentTransform`(실제 component 이동/회전), "Local faces" 대상
  transform의 preview overlay, `createRoiPointTransform`(ROI 절단점
  변환용 행렬) 세 곳 모두 `resolveTransformPivot(rule, node.center)`로
  동일한 pivot을 사용하도록 통일했다.

### API 계약 + 백엔드

- `frontend/src/api/types/raytrace.ts`의 `TransformRule`에 optional
  `pivot` 추가, `ray-tracing-model.ts`가 rule에 pivot이 있을 때만
  포함해서 전송.
- `src/leakage_simulator/raytrace_bridge.py`: bounding-box 중심으로 계산한
  `pivots` dict를, rule에 `pivot`이 있는 component에 한해 그 값으로
  덮어쓰도록 수정. Viewer와 동일한 결과가 나오도록 오버라이드 지점에
  주석으로 동기화 필요성을 명시했다.

## 검증

- 백엔드: `test_raytrace_bridge.py`에 커스텀 pivot이 bbox 중심과 다른
  결과를 낸다는 걸 명시적으로 검증하는 테스트 추가. 전체 92개 통과.
- 프론트: `feature-editors.test.tsx`에 Custom point 토글 → pivot 입력 →
  Apply까지의 실제 플로우 테스트 추가. `tsc -b`, 전체 89개 테스트 통과.
- 실제 브라우저(Playwright + Chrome)로 CAD import → Transform editor →
  Custom point 입력까지 직접 확인. 의도적으로 먼 pivot(X -800mm)에
  90° tilt를 적용해, 작은 부품(Frame_Middle_FMB)이 자기 자리에서
  회전하지 않고 지렛대처럼 멀리 튕겨나가는 것을 스크린샷으로 확인 -
  bbox 중심이 아닌 지정한 점을 기준으로 정확히 회전함을 검증했다.
