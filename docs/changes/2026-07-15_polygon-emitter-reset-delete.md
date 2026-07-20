# Polygon emitter 및 편집 버튼 보완

## 변경 목적

- Reference geometry emitter에서 선택점을 단순히 포함하는 사각 평면뿐 아니라 선택점을 꼭지점으로 활용하는 자동 폐곡선 발광면을 지원한다.
- Emitter properties의 `Reset properties`와 `Delete`가 저장 상태와 preview 상태 모두에서 명확히 동작하도록 수정한다.

## 구현 내용

- Web UI 버전을 `v0.8.3`으로 갱신했다.
- Vertex reference에 `Plane containing vertices`와 `Polygon – Auto closed boundary` 선택 항목을 추가했다.
- Polygon 방식은 선택점을 계산 평면에 투영한 후 convex hull로 선택 순서와 무관한 폐곡선을 만든다.
- 내부점 제외 개수, 경계 꼭지점 개수, 실제 polygon 면적, 평면 이탈 오차를 Geometry details에 표시한다.
- 평면 이탈 오차가 `0.05 mm`를 초과하거나 polygon 면적이 0에 가까우면 Apply를 차단한다.
- Three.js overlay가 사각형이 아닌 실제 polygon 형상을 렌더링하도록 변경했다.
- `EmitterSpec`에 `surface_construction`, `polygon_vertices` 계약을 추가했다.
- ray tracer가 polygon을 면적 가중 삼각형으로 나누어 발광 시작점을 균일 샘플링하도록 변경했다.
- `power_per_area` 계산은 polygon의 실제 면적을 사용한다.
- `Reset properties`는 선택 geometry를 유지하면서 저장값 또는 기본 속성값으로 되돌린다.
- `Delete emitter`는 저장된 emitter를 삭제하고, 신규 생성 중에는 `Discard draft`로 preview를 제거한다.

## 검증

- `python -m py_compile`로 웹 서버와 ray tracer 문법을 확인했다.
- 전체 단위 테스트 8개가 통과했다.
- Polygon 삼각형 면적 `50 mm²`와 `power_per_area` 환산값을 회귀 테스트에 추가했다.
- 200개의 ray 시작점이 삼각 polygon 내부에만 생성되는지 확인했다.
- 브라우저에서 Polygon 전환, Reset 후 geometry 유지, 신규 preview 삭제, 저장 emitter 삭제와 JSON payload 제거를 확인했다.

## 현재 제한

- 자동 폐곡선은 convex hull 방식이므로 오목한 concave polygon은 아직 지원하지 않는다.
- 최대 선택점 수는 기존과 동일하게 6개이다.
