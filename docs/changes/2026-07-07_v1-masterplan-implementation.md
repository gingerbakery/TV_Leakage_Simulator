# 2026-07-07 V1 문서 정비 및 초기 실행 정합성 정리

## 변경 요약
- V1 파이프라인 기본 뼈대를 `src/leakage_simulator`에 정식 반영.
- `.md` 문서 세트(요구사항/설계/소재/시나리오/리스크) 전체 정리.
- 합성 지오메트리 기반 실행 + 면/체적 광원 + gap + 소재 라이브러리 + ray tracing + 지표 산출 완료.
- JSON/CSV/PNG 출력 경로 일원화(`execute_run`) 및 비교 유틸 `compare_outputs` 추가.

## 성능/파라미터(샘플)
- 기본 ray_count: 4000
- 기본 max_depth: 2
- 기본 k_abs: 0.12
- 기본 k_brdf: 1.0

## 테스트 수행(기록)
- 실행 환경: Python 런타임이 설치되지 않아 동적 실행 확인 불가(로컬 런타임 이슈).
- 추후 `py`/`python` 경로 확인 후 다음 단계에서 smoke test를 우선 수행할 예정.

## 다음 액션
- run-time validation: 합성 시나리오 3종 smoke test
- gap=0/0.08 비교 회귀 체크
- 결과 리포트 템플릿(PDF/PNG 조합) 정식화
