# Datum plane CAD Face pick: 클릭 지점 → 선택한 surface의 중심

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 배경

Emitter/Receiver Datum Plane의 "뷰어에서 CAD Face 선택" 기능은 지금까지
마우스로 클릭한 정확한 지점(raycast hit point)을 그대로 Center로
사용했다. 이번 요청은 클릭 지점이 아니라 **선택된 CAD 표면 전체의
정중앙**에 plane을 생성해 달라는 것 - 즉 그 면의 어디를 클릭하든
결과 Center가 항상 그 면의 기하학적 중심으로 동일해야 한다.

## 변경 사항

`frontend/src/features/viewer/three-viewer-canvas.tsx`의
`datumFacePickArmedRef` 클릭 핸들러(Emitter/Receiver 공용 경로)를
수정했다.

- 클릭한 삼각형(`hit.faceId`)을 seed로 기존 `findCoplanarFacePatch`
  (같은 component 안에서 normal이 같고 평면상에 있는 인접 삼각형들을
  flood-fill로 묶는 함수 - Emitter surface 다중 선택에서 이미 쓰던
  것과 동일)를 호출해 그 면 전체의 삼각형 집합(patch)을 구한다.
- patch에 속한 각 삼각형의 `face_centroids`를 `face_areas_mm2`로
  가중평균해 area-weighted centroid를 계산하고, 이것을 Center로
  사용한다(직사각형처럼 대칭인 면에서는 기하학적 중심과 일치한다).
  patch의 총 면적이 0이면(비정상 케이스) 안전하게 기존 클릭 지점으로
  폴백한다.
- normal은 기존과 동일하게 클릭한 삼각형의 `face_normals`를 그대로
  사용한다(같은 patch 안에서는 어차피 거의 동일한 normal이다).
- 상태 메시지를 `face {id} 선택됨` → `surface 중심 선택됨 (N
  triangles)`로 변경해 이제 개별 삼각형이 아니라 면 전체를 인식하고
  있음을 알 수 있게 했다.

## 검증

- 프런트 `tsc -b` 통과. `vitest run` 18 files / 94 tests 통과(영향
  없음 - 순수 뷰어 클릭 핸들러 변경).
- 실제 브라우저(Playwright + 로컬 Chrome)로 STEP 샘플을 import하고
  Receiver Datum Plane 다이얼로그에서 "뷰어에서 CAD Face 선택" 클릭
  후, 같은 넓은 평면 위의 서로 다른 두 지점(화면 좌표 (800,500)과
  (1150,650))을 각각 클릭했다. 두 경우 모두 동일한 8,192-triangle
  patch가 잡혔고 Center 값도 정확히 동일(X=400.0, Y=232.0, Z=33.0)
  했다 - 클릭 지점과 무관하게 면의 중심이 재현됨을 확인.
- `docs/receiver-data-contract.md`의 Datum plane 설명을 새 동작에
  맞게 갱신했다.
