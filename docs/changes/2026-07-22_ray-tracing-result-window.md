# 2026-07-22 Ray tracing 인앱 결과창

## 목적

- 폭이 좁은 왼쪽 `Result` 패널에서 상세 분석 결과와 Receiver heatmap이 잘리는 문제를 해소한다.
- 계산 진행 중에는 기존 왼쪽 게이지만 사용하고, 완료 후 현재 3D viewer 위에서 상세 결과를 확인할 수 있게 한다.

## 변경 사항

- 계산 중에는 별도의 결과창을 열지 않고 왼쪽 Ray tracing 진행률 게이지만 갱신한다.
- 계산이 완료되면 3D viewer 위에 Emitter/Receiver 설정창과 같은 형태의 `Ray Tracing Analysis Result` 플로팅 패널을 표시한다.
- 결과 패널은 설정창보다 넓게 표시하며, 드래그 이동·닫기·내부 스크롤을 지원한다.
- 패널 상단에 LightTools 형식의 탭을 배치하고 선택한 결과 분류만 본문에 표시한다.
  - `Ray summary`: 전체 KPI
  - `Surface optical`: surface optical property 통계
  - `Multi-bounce`: multi-bounce reflection 통계
  - `Receiver`: Receiver별 밝기 지표와 heatmap
- 패널 오른쪽 아래의 크기 조절 핸들을 드래그해 너비와 높이를 변경할 수 있으며, 패널은 3D viewer 영역 밖으로 벗어나지 않는다.
- 왼쪽 `Result` 메뉴에는 3D Ray path 필터와 간단한 상태만 유지한다.
- 결과 패널을 닫은 경우 `분석 결과 보기` 버튼으로 마지막 결과를 다시 표시한다.
- Emitter, Receiver 또는 Transform 변경으로 결과가 무효화되면 인앱 결과 패널을 닫고 재계산을 안내한다.

## 검증

- `python -m py_compile run_web.py`
- 생성된 메인 JavaScript를 `node --check`로 구문 검사
- 로컬 Web UI에서 인앱 결과 패널, 탭 전환, Receiver heatmap 및 패널 크기 조절 확인
