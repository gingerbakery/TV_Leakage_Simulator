# Datum plane CAD Face pick: ROI 절단면(cap)도 선택 가능하도록 지원

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 배경

ROI로 솔리드를 자르면 원본 CAD face가 아닌, ROI 박스 경계에서 새로
생성된 단면(section cap)이 노출된다. 지금까지 "뷰어에서 CAD Face
선택"은 이 단면을 클릭하면 무조건 "ROI 절단면은 원본 CAD face가
아니므로 선택할 수 없습니다"로 거부했다. 그런데 사용자 워크플로우
중에는 정확히 이 절단면을 기준점으로 잡고 거기서 Offset을 줘서
수광부를 배치하려는 경우가 있는데, 이 거부 때문에 애초에 기준점을
잡을 수조차 없어 offset 개념을 쓸 수 없었다.

## 변경 사항

`frontend/src/features/viewer/three-viewer-canvas.tsx`:

- `resolveSurfaceHit`가 이제 raycast로 맞은 삼각형의 world-space
  normal(`hit.face.normal`을 `object.matrixWorld`로 변환)과, 맞은
  오브젝트가 ROI 절단면 mesh(`name === 'roi-section-caps'`)인지
  여부(`isRoiCap`)도 함께 반환한다.
- `datumFacePickArmedRef` 클릭 핸들러: `faceId === null`인 히트가
  ROI 절단면이면(원본 CAD topology가 없어 "면 전체의 중심"을
  flood-fill로 계산할 수 없으므로) 더 이상 거부하지 않고, **클릭한
  정확한 지점**을 Center로, normal을 가장 가까운 세계 좌표축(±X/±Y/
  ±Z 중 하나 - 절단면은 항상 ROI 박스 경계에 축정렬되어 있음)으로
  스냅한 값을 Rotation으로 채운다. 원본 CAD face(사각형 안 어디를
  클릭해도 그 면 전체의 중심을 잡는 기존 동작)와는 의도적으로 다른
  동작이다 - 절단면은 "하나의 고정된 면적"이라는 개념이 없어서
  클릭 지점 자체가 가장 합리적인 기준점이다.
- 절단면이 아닌, 진짜로 선택할 수 없는 히트(이론상으로만 존재)에
  대해서는 여전히 거부하되 메시지를 "선택할 수 없는 표면입니다"로
  일반화했다.

## 검증

- 프런트 `tsc -b` 통과. `vitest run` 18 files / 96 tests 통과
  (영향 없음 - 순수 뷰어 클릭 핸들러 변경).
- 실제 브라우저(Playwright + 로컬 Chrome)로 STEP 샘플 import 후,
  Y축 방향으로 솔리드를 자르는 ROI를 만들고 `-ZX` 뷰에서 노출된
  절단면을 클릭 → 더 이상 거부되지 않고 Center가 클릭 지점(Y≈5.6,
  ROI 경계 근처)으로, Rotation이 축정렬된 90°(Y축 normal)로 정확히
  채워지는 것을 확인. 스크린샷으로 수광부 미리보기 평면이 절단면
  위 클릭 지점에 정확히 놓이는 것도 확인했다.
