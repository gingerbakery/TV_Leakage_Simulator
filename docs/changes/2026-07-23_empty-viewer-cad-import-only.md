# 초기 빈 Viewer 및 CAD Import 단일화 (Web UI v0.9.26)

## 요청 배경

- 실제 CAD 도면으로 테스트하는 단계에 진입하여 데모 CAD와 합성 Sample model이 더 이상 필요하지 않다.
- 페이지를 처음 열었을 때 블록 3개로 구성된 합성 형상이 자동 표시되어 실제 작업 시작 상태와 혼동됐다.

## 변경 내용

- Model import 메뉴에서 `Import CAD` 외의 버튼을 제거했다.
  - `Load demo CAD` 제거
  - `Use sample model` 제거
- 선택 파일 표시의 초기값을 `No CAD selected`로 변경했다.
- 페이지 초기화 과정의 자동 `loadScene()` 호출을 제거했다.
- 초기 상태에서는 mesh와 component가 생성되지 않으며 3D Viewer를 빈 화면으로 유지한다.
- 빈 CAD 경로로 `loadScene()`이 호출되더라도 합성 형상을 만들지 않고 즉시 종료한다.
- `/api/scene`도 CAD 경로가 없으면 `400 CAD file is required`를 반환하도록 변경했다.

## 기대 동작

1. 페이지를 처음 열면 Viewer에 모델, 축, 데모 블록이 표시되지 않는다.
2. Model import에는 `Import CAD`만 표시된다.
3. 사용자가 CAD 파일을 선택하고 업로드한 뒤에만 mesh와 component tree가 생성된다.

