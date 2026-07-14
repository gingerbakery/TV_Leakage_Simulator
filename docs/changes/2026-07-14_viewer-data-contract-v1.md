# 2026-07-14 3D Viewer 데이터 계약 v1

## 요약
- Three.js viewer 전환을 준비하기 위해 mesh/component/face id 계약을 명시했다.
- 기존 Canvas viewer 호환성을 유지하면서 신규 필드를 scene payload에 추가했다.

## 변경
- `src/leakage_simulator/roi.py`
  - `schema_version`
  - `units`
  - `coordinate_system`
  - `mesh.face_ids`
  - `mesh.face_component_ids`
  - `mesh.face_material_ids`
  - `mesh.face_normals`
  - `mesh.face_centroids`
  - `mesh.face_areas_mm2`
  - `components` alias
- `docs/viewer-data-contract.md`
  - Three.js viewer가 사용할 mesh/component/face id 규칙 정의

## 의도
- 3D viewer, ROI, transform, material, ray path overlay가 같은 식별자 체계를 공유하도록 한다.
