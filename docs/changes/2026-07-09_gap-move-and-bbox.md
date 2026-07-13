# 2026-07-09 gap move / bbox 구현

## 목적
- 기존 face 기반 gap 외에, 기구 설계자가 더 직관적으로 사용할 수 있는 gap 생성 방식을 추가했다.

## 반영 내용
- `component_move_gap` 추가
  - 선택한 component를 x/y/z 방향으로 이동한다고 가정
  - 입력한 이동 벡터를 기준으로 gap 크기를 산정
  - 내부 계산은 face-level gap으로 변환하여 기존 ray tracer와 연동
- `bbox_gap` 추가
  - 사용자가 지정한 3차원 박스 내부의 face centroid를 gap 대상으로 선택
- 공용 component 분해 로직을 `src/leakage_simulator/components.py`로 분리
- Web UI를 `v0.4.0`으로 갱신
  - `ROI face 기준`
  - `Component 이동`
  - `3차원 공간 지정`
  - 세 가지 gap 모드 제공

## 구현 원칙
- 실제 geometry를 직접 이동시키지 않는다.
- 대신 `GapRule -> face targets -> GapSample` 흐름을 유지한다.
- 즉, move/bbox 입력도 최종적으로는 face 단위 gap으로 해석된다.

## 검증
- `py_compile` 문법 검증 통과
- synthetic scene 기준 `component_move_gap` 샘플 생성 확인
- synthetic scene 기준 `execute_run(...)` 실행 확인
