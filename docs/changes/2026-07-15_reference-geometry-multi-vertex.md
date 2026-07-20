# Web UI v0.8.2 - Reference geometry 다중 vertex 선택

## 변경 목적

- 세 번째 vertex 이후 새 점을 선택하면 기존 점이 자동 교체되어 선택 결과를 예측하기 어려운 문제를 해결한다.
- Emitter와 Receiver에서 넓거나 복잡한 영역을 여러 기준점으로 정의할 수 있도록 한다.
- `Reset` 버튼이 선택점 초기화인지 속성 초기화인지 불명확했던 UI를 분리한다.

## 선택 동작

- vertex 방식은 최소 3개, 최대 6개를 지원한다.
- 3개가 선택되는 순간 평면 preview를 생성한다.
- 네 번째부터 여섯 번째 점은 기존 점을 삭제하지 않고 누적한다.
- 이미 선택된 vertex를 다시 클릭하면 해당 점만 제외한다.
- 6개가 선택된 상태에서 새 점을 누르면 기존 선택을 유지하고 최대 개수 안내를 표시한다.

## 선택점 초기화

- Emitter popup에 `Clear selected points` 버튼을 추가했다.
- Receiver popup에 `Clear selected points` 버튼을 추가했다.
- edge 방식에서는 버튼 문구가 `Clear selected edges`로 바뀐다.
- 선택 개수를 `현재 개수 / 최대 개수`로 표시한다.
- 기존 `Reset` 버튼은 `Reset properties`로 변경해 크기, power, 방향, 해상도 등의 속성 초기화임을 명확히 했다.

## 평면 계산

- 선택한 vertex 전체의 중심점을 계산한다.
- 가장 멀리 떨어진 두 점을 이용해 U축을 정한다.
- U축에서 가장 멀리 떨어진 점 방향을 직교화해 V축을 정한다.
- 모든 선택점을 U/V 평면에 투영해 전체 점을 포함하는 width, height와 center를 계산한다.
- 점들이 완전히 같은 평면에 있지 않으면 Geometry details에 planarity deviation을 표시한다.

## 데이터 계약

- 기존 `reference_mode=three_vertices` 값은 하위 호환을 위해 유지한다.
- `reference_vertex_indices`에는 선택된 3~6개 vertex ID를 순서대로 저장한다.
- Emitter와 Receiver 모두 동일한 선택 규칙을 사용한다.

## 검증 항목

- Emitter/Receiver에서 3~6개 vertex 누적
- 선택 vertex 재클릭 시 개별 제외
- Clear selected points 동작
- 6개 초과 선택 방지
- 다중 vertex 계약 직렬화 및 회귀 테스트

## 검증 결과

- Python 및 렌더링 JavaScript 문법 검사 통과
- 전체 단위 테스트 6건 통과
- Receiver에서 vertex 선택 후 `Reset properties`를 눌러도 선택점이 유지되는 것을 확인
- Receiver에서 `Clear selected points` 실행 시 선택 개수가 0으로 초기화되는 것을 확인
- Emitter와 Receiver 모두 `3–6 vertices`, 선택 개수, 명시적 초기화 버튼이 표시되는 것을 확인
- 브라우저 console error 없음
