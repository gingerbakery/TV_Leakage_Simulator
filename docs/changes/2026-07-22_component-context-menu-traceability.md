# 2026-07-22 Component 컨텍스트 메뉴와 Traceability

## 목적

- 3D viewer에서 부품을 우클릭해 자주 사용하는 Component 명령에 바로 접근한다.
- 부품을 화면에 유지하면서 Ray Tracing 충돌 대상에서만 제외할 수 있게 한다.

## 변경 사항

- Full CAD View와 ROI View의 Component를 우클릭하면 다음 메뉴가 표시된다.
  - `Hide` / `Show`
  - `Traceability Off` / `Traceability On`
  - `Material`
  - `Transform`
  - `Delete…`
- Component Tree의 각 행에서도 같은 우클릭 메뉴를 사용할 수 있다.
- Component Tree 행에는 `Material`, `Transform`, `Trace Off/On`과 `+` 버튼을 정돈된 한 줄로 표시한다.
- `+` 버튼에는 `Hide/Show`와 `Delete…`만 배치하며, 우클릭 메뉴는 전체 명령을 유지한다.
- Component 이름은 한 줄 말줄임으로 표시하고, 전체 이름은 마우스를 올렸을 때 확인할 수 있다.
- 비충돌 부품은 `Trace On` 버튼, 메타 정보와 주황색 상태로 표시한다.
- `Traceability Off`는 Component 형상, 표시, Material 및 Transform 설정을 유지한다.
- Ray Tracing 실행 시 삭제된 Component와 Traceability가 꺼진 Component만 충돌 메시에서 제외한다.
- `Delete…`는 기존과 동일하게 확인창을 거쳐 Component 및 연결 설정을 제거한다.
- 단순 우클릭은 메뉴를 열고, 우클릭 드래그는 기존 카메라 pan 동작을 유지한다.
- 3D viewer 또는 Component Tree에서 Component를 일반 클릭하면 선택만 수행하며 Transform popup은 자동으로 열지 않는다.
- Transform popup은 Component Tree의 `Transform` 버튼 또는 우클릭 메뉴에서 `Transform`을 명시적으로 선택할 때만 연다.

## 검증

- Python 및 생성 JavaScript 구문 검사
- Ray Tracing bridge와 ray tracer 회귀 테스트
- Component Tree와 3D viewer의 우클릭 메뉴 및 각 연계 동작 확인
- Traceability Off 상태에서 형상 표시 유지 및 Ray Tracing 충돌 제외 확인
