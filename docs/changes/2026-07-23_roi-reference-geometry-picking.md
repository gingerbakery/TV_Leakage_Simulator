# ROI 절단 Reference Geometry 선택 보완 (Web UI v0.9.25)

## 확인된 문제

- ROI View 표면은 화면 표시를 위해 원본 triangle을 절단한 새로운 mesh를 사용하지만, 기존 pick 로직은 클릭 결과를 다시 원본 triangle vertex로 변환했다.
- ROI 절단으로 새로 생긴 vertex는 화면에는 보여도 Emitter/Receiver reference vertex로 저장되지 않았다.
- 절단 단면 cap은 pick 후보에 포함되지 않았다.
- Edge 방식은 화면의 CAD 경계선이 아니라 triangle 내부 분할 edge를 선택할 가능성이 있었다.
- Wireframe 모드에서는 화면에 보이는 `wirefill`이 아닌 숨겨진 surface만 raycast하여 Reference 선택이 실패할 수 있었다.
- ROI View의 Fit이 전체 CAD 경계를 기준으로 계산되어 절단 영역이 작게 표시되고 정밀 선택이 어려웠다.

## 변경 내용

- ROI 절단 표면과 cap의 실제 Three.js triangle vertex 좌표를 reference pick 결과로 사용한다.
- Vertex 선택은 클릭 위치에서 28 px 이내의 표시 vertex만 스냅한다.
- Edge 선택은 화면에 표시되는 CAD feature edge와 ROI 절단 경계 edge를 직접 raycast한다.
- Wireframe에서는 표시 중인 `wirefill` geometry를 pick 대상으로 사용하고 Surface 모드에서는 surface geometry를 사용한다.
- 원본 index가 없는 절단 vertex/edge를 위해 좌표 기반 필드를 추가했다.
  - `reference_vertex_points`
  - `reference_edge_points`
- 기존 index 필드는 원본 CAD vertex/edge인 경우에만 호환용으로 유지한다.
- 원본 index가 없는 절단점을 JavaScript가 `null → 0`으로 변환하지 않도록 index 판정을 엄격하게 변경했다. 절단점은 좌표만 저장하며 임의의 원본 vertex ID를 만들지 않는다.
- Emitter와 Receiver의 생성, 편집, 초기화, 삭제, JSON 전달 경로를 동일한 좌표 계약으로 통일했다.
- ROI View Fit 및 axis/marker 크기는 절단된 실제 geometry bounding box를 기준으로 계산한다.

## 검증 항목

- 기존 Emitter/Receiver index 계약의 역호환
- 좌표 기반 reference 계약의 직렬화 및 역직렬화
- Datum, CAD surface, Reference, Current view 생성 경로 유지
- 기존 RT 회귀 테스트 전체 통과

## 검증 결과

- 일반 CAD vertex를 Reference Emitter에서 클릭해 선택 카운트가 `0 → 1`로 증가하는 것을 확인했다.
- ROI 절단 후 새로 생긴 vertex를 Reference Emitter와 Reference Receiver에서 각각 선택할 수 있음을 확인했다.
- ROI 절단 vertex 3개로 Reference Receiver를 실제 생성하고 좌표 기반 JSON이 저장되는 것을 확인했다.
- 원본 vertex ID가 없는 좌표만으로 Emitter/Receiver 계약을 직렬화·복원하는 회귀 테스트를 추가했다.
- Python 전체 테스트 `76개`가 통과했다.
