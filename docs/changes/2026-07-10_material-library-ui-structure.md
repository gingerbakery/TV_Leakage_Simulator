# Material Library UI 화면 구조 설계

## Summary
- Material Library 전용 UI 구조를 별도 문서로 정의했다.
- `Target -> Quick Assign -> Surface Finish -> Advanced Optical -> Assignments -> Library Manager` 순서로 화면 구조를 고정했다.
- `Components` 탭과의 연결 방식, `3D viewer` 하이라이트 규칙, `Part assignment / Face override` 작업 흐름을 포함했다.

## Added documents
- `docs/material-library-ui.md`

## Key decisions
- `Part assignment`를 기본 작업 흐름으로 둔다.
- `Face override`는 유지하되, target이 명확히 선택된 경우만 활성화한다.
- BSDF는 고급 섹션으로 분리한다.
- Material Library는 라이브러리 편집창이면서 동시에 현재 프로젝트 assignment 관리자 역할을 같이 가진다.

## Follow-up
1. `run_web.py`의 Material 탭을 위 구조 기준으로 재편
2. Components row의 `Material` 버튼과 target 동기화 강화
3. Assignment 데이터 구조와 UI 상태를 연결
