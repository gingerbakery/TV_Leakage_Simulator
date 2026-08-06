# Pivot 위치 마커 + Pan 속도 재조정

- 날짜: 2026-08-04
- 대상 브랜치: `main`

## Pan 속도 2배 복원

우측 드래그 Pan이 너무 느려졌다는 피드백으로 이전 조정값을 2배로
되돌렸다: Full view `0.15 → 0.3`, ROI view `0.03 → 0.06`
(`three-viewer-canvas.tsx`, `controls.panSpeed` 초기값과 ROI 상태별
동적 조정 두 곳 모두).

## Tilt pivot 위치 마커

Custom pivot을 찍거나 숫자로 입력해도 실제로 어디를 가리키는지 화면에
아무 표시가 없었다. Transform editor가 열려 있고 Custom point 모드일
때, 그 지점에 작은 핑크색 구체 + 3축 십자선 마커를 표시한다.

- `workspace-store.ts`: `pivotPreviewPoint: Vector3Value | null` 추가
  (휘발성 UI 상태, 프로젝트 저장 대상 아님).
- `transform-editor-dialog.tsx`: dialog가 열려 있고 `pivotMode ===
  'custom'`인 동안 draft `pivot` 값을 실시간으로
  `actions.setPivotPreviewPoint`에 반영 - Apply를 누르기 전에도, 숫자를
  손으로 조정하는 중에도 마커가 따라 움직인다. Dialog가 닫히거나
  center 모드로 돌아가면 즉시 지운다.
- `three-viewer-canvas.tsx`: `createPivotMarker()`가 작은 구체 +
  3축 십자선을 만든다 (`depthTest: false`라 모델 내부에 있어도 항상
  보임, 크기는 `originAxisBaseScale` 기준으로 모델 스케일에 맞춰
  조정). `pivotMarkerRoot`는 `threeScene`의 최상위 자식이라 메인 뷰와
  Full View PIP 양쪽에 모두 그려진다 (ROI 상태에 따라 껐다 켜는 대상이
  아님).

## 검증

- `tsc -b`, `vitest run` 18 files / 91 tests 통과 (기존 스위트,
  마커 자체는 별도 유닛 테스트 없이 시각 검증으로 확인).
- 실제 브라우저(Playwright + Chrome)로 확인: Custom point 진입 시
  bbox 중심에 마커가 뜨고, 뷰어에서 다른 지점을 클릭하면 마커가 정확히
  그 지점(모델 모서리)으로 이동하는 것을 스크린샷으로 확인했다.
