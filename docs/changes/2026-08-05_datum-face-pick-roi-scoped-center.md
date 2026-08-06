# Datum plane CAD Face pick: ROI로 잘린 면은 보이는 부분만으로 중심 계산

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 배경

[[datum-face-pick-surface-center]]에서 클릭한 CAD 표면 전체의
area-weighted 중심을 Center로 쓰도록 바꿨는데, 이 계산이
`component.face_indices`(그 컴포넌트의 **원본, 잘리지 않은** 전체
삼각형 목록)를 기준으로 flood-fill을 했다. 그래서 ROI로 면의 일부만
잘려서 보이는 상태에서 그 보이는 부분을 클릭해도, 화면에 없는
나머지(원본 전체 면)까지 다 포함해서 중심을 계산해버렸다 - 결과적으로
"보이는 면의 중심"이 아니라 사실상 "원본(전체 도면) 면의 중심"이 나온
것. 사용자가 "ROI Cut된 도면의 선택 face 중앙이 아니라 전체 도면
중앙을 잡는다"고 보고한 것과 정확히 일치.

## 변경 사항

`frontend/src/features/viewer/three-viewer-canvas.tsx`:

- `roiFaceIds` prop(현재 활성 ROI 스코프에 포함된 원본 face id 목록 -
  이미 `viewer-workspace.tsx`에서 계산해 내려주던 값)을 클릭 핸들러
  안에서도 최신 값으로 읽을 수 있도록 `roiFaceIdsRef` + 동기화
  `useEffect`를 추가했다(핸들러가 정의된 effect는 `scene`이 바뀔
  때만 재생성되므로 ref 없이는 stale closure가 된다).
- `datumFacePickArmedRef` 클릭 핸들러에서, `runtime.roiPreviewRoot.
  visible`이 true(= 지금 ROI로 잘린 뷰를 보고 있는 상태)이면
  `findCoplanarFacePatch`에 넘기는 candidate face 목록을
  `component.face_indices` 전체가 아니라 `roiFaceIdsRef.current`와
  교집합한 것으로 제한한다. ROI 밖의 원본 삼각형은애초에 후보에서
  빠지므로, flood-fill이 ROI 경계에서 자연스럽게 멈춘다.
- ROI가 활성화되지 않은(전체 뷰) 상태에서는 기존과 동일하게 컴포넌트
  전체 face_indices를 그대로 쓴다 - 이번 변경은 ROI 뷰에서만
  영향을 준다.

## 검증

- 프런트 `tsc -b` 통과. `vitest run` 18 files / 96 tests 통과
  (영향 없음 - 순수 뷰어 클릭 핸들러 변경).
- 실제 브라우저(Playwright + 로컬 Chrome)로 STEP 샘플 import 후,
  모델의 큰 상판을 Y축 방향으로 중간에서 자르는 ROI(X 290.9~777.5,
  Y 3.5~286.6, Z 0~45)를 만들고, 그 안에서 보이는 상판을 클릭 →
  3,033개 삼각형 patch, Center (523.3, 162.2, 33.0)로 ROI 범위
  중앙 부근에 정확히 잡히는 것을 확인(ROI 밖 원본 면 전체를 포함하면
  나왔을 8,192-triangle · 훨씬 다른 중심 좌표와 대비됨). 스크린샷으로
  중심 마커가 실제로 화면에 보이는 잘린 면 한가운데에 찍히는 것도
  확인했다.
