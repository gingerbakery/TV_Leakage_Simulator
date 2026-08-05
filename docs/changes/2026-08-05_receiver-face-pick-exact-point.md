# Receiver CAD face 선택: 삼각형 centroid 대신 클릭 지점 그대로

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 변경 사항

Datum plane receiver의 "뷰어에서 CAD Face 선택"이 지금까지는 클릭한
삼각형(triangle facet)의 centroid를 Center로 사용했다. 사용자가
마우스로 클릭한 정확한 표면 교차점을 그대로 쓰길 원해서,
`three-viewer-canvas.tsx`의 receiver face pick 처리를
`scene.mesh.face_centroids[faceId]` 대신 raycast hit point
(`hit.point`)를 사용하도록 변경했다. Normal은 여전히 클릭한 삼각형의
`face_normals[faceId]`를 사용한다(점 하나로는 방향을 알 수 없으므로).

## 검증

- `tsc -b`, `vitest run` 18 files / 94 tests 통과.
