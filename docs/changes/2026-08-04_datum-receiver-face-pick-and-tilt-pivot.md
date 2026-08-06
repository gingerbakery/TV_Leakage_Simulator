# Datum plane Receiver: CAD face 기반 배치 + offset + tilt pivot

- 날짜: 2026-08-04
- 대상 브랜치: `main`

## 배경

Step 04 Ray tracing의 "Datum plane" receiver는 지금까지 CAD와 무관하게
중심 X/Y/Z, 회전 Rx/Ry/Rz를 직접 타이핑하는 방식뿐이었다. 요청:

1. 기구 도면의 face를 선택해서 그 위치를 기준으로 삼고, 거기서
   X/Y/Z로 offset할 수 있게 해달라.
2. Receiver 회전(Tilt)도 방금 만든 component Transform의 custom pivot과
   동일한 방식으로 - 지정한 점을 기준으로 회전되게 해달라.

## 변경 사항

### 데이터 모델

- `src/leakage_simulator/types.py`의 `ReceiverSpec`에 `pivot:
  Optional[Vec3] = None` 추가(+ `__post_init__` 정규화). ray trace
  계산 자체에는 쓰이지 않는다(프런트가 최종 `center`/`normal`/`u_axis`/
  `v_axis`를 미리 계산해서 보낸다) - 저장된 receiver를 편집기로 다시
  불러올 때만 필요. `ReceiverSpec.from_dict`가 `cls(**payload)`로
  엄격하게 구성되므로, 필드를 추가하지 않고 프런트가 `pivot` 키를
  보내면 바로 예외가 났을 것.
- `frontend/src/api/types/raytrace.ts`의 `ReceiverSpec`에도 동일하게
  `pivot: Vec3 | null` 추가.

### `ray-tracing-model.ts`

- `axesFromNormal(normal)` 추가: 법선 하나만으로 안정적인 (u, v) 직교
  기저를 만든다(참조축으로 world Z 또는 X 중 법선과 덜 평행한 쪽을
  선택). CAD face pick으로 얻은 normal을 기존 Rotation X/Y/Z 필드로
  표현하기 위해 `rotationFromPlaneAxes(uAxis, vAxis, normal)`과 함께
  사용한다.
- `createDatumReceiver()` 시그니처에 `positionOffset`, `pivot` 파라미터
  추가. 계산: `centerBeforeTilt = baseCenter + offset`,
  `pivotPoint = pivot ?? centerBeforeTilt`,
  `center = pivotPoint + rotate(centerBeforeTilt - pivotPoint,
  rotationDeg)`. pivot이 없으면(기본값) delta가 0이라 회전해도 중심이
  그대로 유지 - 이번 변경 전 동작과 동일. `normal`/`u_axis`/`v_axis`는
  이전과 동일하게 `planeAxesFromRotation(rotationDeg)`로 직접 계산되며
  피벗의 영향을 받지 않는다(피벗은 위치에만 영향).

### Viewer (`three-viewer-canvas.tsx`)

- component Transform의 pivot pick 메커니즘(`pivotPickArmed`/
  `pivotPickPoint`/`pivotPreviewPoint`, edge 끝점/중간점 스냅 포함)을
  그대로 재사용한다 - 이미 컴포넌트에 종속되지 않는 범용 store
  필드였기 때문에 추가 작업 없이 그대로 동작한다.
- 새 store 상태 `receiverFacePickArmed`/`receiverFacePickResult` 추가.
  armed 상태에서 CAD face를 클릭하면(ROI 절단면이 아닌 원본 face)
  `scene.mesh.face_centroids[faceId]`/`face_normals[faceId]`를 그대로
  결과로 기록한다 - 새 backend 호출이나 데이터 불필요, 이미
  scene payload에 있는 값.

### `ray-tracing-panel.tsx` (`ReceiverDialog`, `datum_plane` 모드만)

- "뷰어에서 CAD Face 선택" 버튼: armed 상태에서 face를 클릭하면 그
  centroid를 Center에, normal로부터 계산한 회전을 Rotation에 채운다.
- "Offset (mm)" 필드 추가(기존에 있던 `positionOffset` state를
  datum_plane에도 연결).
- "Tilt pivot" 섹션 추가: component Transform editor와 동일한 UI
  (Receiver center / Custom point 토글, X/Y/Z 입력, "뷰어에서 좌표
  선택" 버튼) - store 필드를 공유하므로 피벗 마커(핑크 구체+십자선)도
  그대로 재사용된다.
- 기존에 `mode === 'datum_plane'`일 때 `center`/`normal`/`u_axis`/
  `v_axis`를 raw state로 다시 덮어쓰던 코드를 제거했다 - 이제
  `createDatumReceiver()`가 offset·pivot까지 반영한 최종값을 정확히
  계산해 주므로 덮어쓰면 그 결과가 무시되는 문제가 생긴다.
- 다시 열어 편집할 때는 `base_center`(datum_plane에서 새로 채워짐,
  과거 저장된 receiver는 `center`로 폴백)에서 Center를, `pivot` 유무로
  Tilt pivot 모드를 복원한다.

## 검증

- 백엔드 92개 테스트 통과(신규 optional 필드, 기존 동작 불변).
- 프런트 `tsc -b` 통과, `vitest run` 18 files / 94 tests 통과 - 신규
  테스트로 (a) pivot 없이 offset만 적용 시 중심 이동·회전 무관 확인,
  (b) 커스텀 pivot으로 90도 회전 시 실제로 그 점을 중심으로
  공전하는지(100,0,0 → 0,100,0) 확인, (c) `axesFromNormal`의 직교성
  확인.
- 실제 브라우저(Playwright + Chrome)로 전체 플로우 확인: CAD import →
  Step 04 → Datum plane receiver → CAD face 클릭(중심·회전 자동 반영)
  → Offset Z=20 입력 → Custom point 전환 → 뷰어에서 모서리 클릭(edge
  스냅으로 정확히 800,450,45 선택, Transform pivot과 동일한 스냅
  로직임을 확인) → Rotation Z=30 → Add receiver까지 에러 없이 완료,
  Receiver 목록에 `receiver_001 · datum plane · 30 × 30 mm` 로 반영됨을
  확인했다.
