# 2026-07-14 Three.js viewer 전환 계획

## 요약
- 전체 프레임워크 전환 전에 3D viewer만 Three.js로 단계 교체하는 전략을 문서화했다.
- `mesh-scene.v1` 데이터 계약을 기준으로 viewer, ROI, transform, ray overlay를 순차 연결하는 방향을 정했다.

## 추가
- `docs/threejs-viewer-migration.md`

## 갱신
- `docs/design.md`
- `docs/web-ui.md`

## 결정
- 회사 PC 시연 안정성을 고려해 CDN only 방식은 기본값으로 쓰지 않는다.
- 우선 후보는 local vendor Three.js 파일을 패키지에 포함하는 방식이다.
