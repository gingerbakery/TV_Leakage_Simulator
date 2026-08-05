# ROI 박스 선택 arm 시 기본 뷰를 "가장 가까운 축" 대신 항상 XY로 고정

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 배경

[[roi-arm-camera-preset-sync]]에서 "arm 시 자동 스냅된 축이 툴바에
반영 안 되던" 라벨 desync 버그를 고쳤지만, 사용자가 실제로 겪던
불편은 라벨이 아니라 **자동 스냅 자체가 예측 불가능하게 느껴진다**는
것이었다.

원인을 재현해서 확인한 내용: "가장 가까운 축으로 스냅"하는 계산
(`nearestRoiCameraPreset`, 현재 카메라 방향과 6개 축 방향의 내적
비교)은 수학적으로는 정확히 동작했다. 문제는 테스트에 쓰인 모델(가로
800mm × 세로 335mm × 두께 45mm의 얇고 넓은 패널)의 **기본 Iso 시작
각도 자체가 이미 YZ 쪽에 상당히 가깝다**는 점 - Iso 방향 벡터
`(1,-1,0.78)`과 YZ의 `(1,0,0)`의 내적이 XY의 `(0,0,1)`보다 훨씬 크다.
그래서 사용자가 마우스로 어느 정도 회전시켜 "XZ에 가깝다"고 느껴도,
그 편향을 넘어설 만큼 크게 돌리지 않으면 여전히 YZ가 선택됐다. YZ는
이 모델처럼 한 축이 아주 얇은 경우 실선처럼 보이는 사실상 쓸모없는
엣지뷰라서 특히 눈에 띄었다.

## 변경 사항

`frontend/src/features/viewer/three-viewer-canvas.tsx`: ROI arm
이펙트에서 처음 arm될 때(`roiSelectionCameraPose`가 아직 없을 때)
`nearestRoiCameraPreset(runtime)`로 "현재 카메라와 가장 가까운 축"을
계산하던 것을 제거하고, 항상 고정값 `'XY'`(정면에서 내려다보는
plan view)로 시작하도록 변경했다. 더 이상 쓰이지 않는
`nearestRoiCameraPreset` 함수도 삭제했다.

- 대부분의 CAD 모델(특히 이 프로젝트가 다루는 TV/패널류)은 XY가 ROI
  박스를 그리기에 가장 유용한 뷰라는 전제.
- arm된 이후 사용자가 직접 XY/-XY/YZ/-YZ/ZX/-ZX 버튼을 눌러 다른
  축으로 전환하는 기존 경로는 그대로 유지된다 - 이번 변경은 오직
  "최초 arm 시 기본값"에만 영향을 준다.
- 이전 [[roi-arm-camera-preset-sync]]에서 추가한
  `onCameraPresetChange` 콜백 배선은 그대로 유지 - 이제는 "예측
  불가능한 스냅 결과"가 아니라 "항상 XY"를 툴바에 정확히 반영하는
  역할을 한다.

## 검증

- 프런트 `tsc -b` 통과. `vitest run` 18 files / 94 tests 통과.
- 실제 브라우저(Playwright + 로컬 Chrome)로 STEP 샘플 import 후,
  마우스로 임의 방향 회전(기본 Iso에서 프리핸드 드래그) 후 arm →
  `XY` 버튼이 `aria-pressed="true"`, 상태바 `ROI 박스 선택 · XY
  view` 정확히 확인. 스크린샷으로 패널 전체가 보이는 정상적인
  top-down plan view로 시작하는 것도 확인 (이전엔 프리핸드 회전
  각도에 따라 얇은 실선 엣지뷰로 시작하는 경우가 잦았음).
