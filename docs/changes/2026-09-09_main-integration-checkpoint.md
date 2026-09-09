# main 통합 체크포인트 — 발광·반사·ROI·조작 개선

## 커밋 범위

사용자의 요청에 따라 지금까지 확인한 다음 변경을 main에 함께 기록한다.

| 기능 | 세부 이력 |
| --- | --- |
| 최대 반사 횟수 1,000회 및 배치 메모리 보호, 결과 종료 사유 표시 | `2026-09-08_reflection-depth-1000.md` |
| 사각형/원형 Target 방향 한정 발광, UI·저장 계약 | `2026-09-08_emitter-aim-area.md` |
| 원본 CAD 면 선택과 ROI 가상 절단면 구분 | `2026-09-08_roi-cad-face-selection.md` |
| ROI 절단면 빗금 표시 | `2026-09-08_roi-section-hatching.md` |
| 숫자 입력 포커스 시 값과 커서 유지 | `2026-09-08_number-input-caret-editing.md` |
| 시야각·줌을 반영한 우클릭 pan 속도 보정 | `2026-09-09_viewer-pan-projection.md` |

기능 문서, 검증 스크립트 및 회귀 테스트를 포함한다. 이전 문서의 미커밋/미푸시 설명은 해당 작업 당시의 상태를 기록한 것이며, 본 체크포인트에서 함께 커밋 대상으로 정리한다.

## 통합 직전 확인

- 기준 커밋: `d991858b0626c8bd2f2b713b14d385d82ffab5c1`.
- `git fetch origin` 후 로컬 main과 origin/main 차이 0/0 확인.
- 프런트엔드 전체 테스트: 36개 파일, **236개 통과**. TypeScript 및 production build 통과.
- lint 오류 0개. 기존 결과창 Hook 의존성 경고 1개와 큰 번들 경고는 유지한다.
- 커밋 직전 CPU 회귀 재검증: Emitter Aim 12개, raytrace bridge 17개, 다회반사 7개, **합계 36개 통과**.
- GPU 실기 검증을 이번 커밋 준비에서 다시 수행하지 않았다. 기존 GPU 측정 조건·결과 및 알려진 테스트 실패는 반사 상한/Aim 변경 문서에 기록되어 있다. 전체 백엔드 테스트가 모두 통과했다는 의미는 아니다.
- 기존 사용자가 열어 둔 작업 탭이나 서버를 재시작하지 않았다.

## 제외 및 작업 경계

- 작업 폴더: `C:/Users/Administrator/Documents/TV leakage simulator main`.
- 원격: `https://github.com/gingerbakery/TV_Leakage_Simulator.git`.
- 별도 `codex/rendering-studio-prototype-20260908` 브랜치와 작업 폴더의 렌더링 개발은 병합하거나 변경하지 않는다.
- CAD 원본, `.bitsam`, 런타임, 빌드 산출물, 로그 및 로컬 전용 협업 규칙 초안은 커밋 범위에서 제외한다.
- 강제 push 없이 일반 main push로 반영하며, 원격이 먼저 변경되면 덮어쓰지 않는다.
