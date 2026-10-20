# 독립 검증 첫 임무: PERF-5D 근거 검토

## 목적

임무 ID: `2026-10-19-perf5d-review-01`

사용자가 요청한 방법 A의 검증 담당 한 명을 운영한다. 이번 임무는 기존
PERF-5D 준비 비용·캐시 변경의 소스, 테스트, 실행 기록, 보고서를 독립적으로
검토하는 것이다. 새로운 GPU 실행이나 제품 수정은 포함하지 않는다.

먼저 `../ai-validation-subagent.md`, repository `AGENTS.md`, GPU 실행 지침
세 문서를 전부 읽고 시작 확인을 제출한다. 이 임무에서 기존 실행 기록의
검증은 새 실행 성공 또는 전체 PERF-5D 완료를 의미하지 않는다.

## 고정 입력

- Delivery: source checkout.
- 원본 repository: `C:\Users\Administrator\Documents\TV leakage simulator main`
- Branch / commit: `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`
- 미커밋 변경: 있음. HEAD만이 아니라 현재 파일을 snapshot으로 복사했다.
- Snapshot: `outputs/validation/2026-10-19-perf5d-review-01/snapshot/`
- Manifest: `outputs/validation/2026-10-19-perf5d-review-01/manifest.json`
- Manifest SHA-256: `DA8AF1758B4FD5A4EFF09EA0CC107AFF87C11E8B8F720BE7472644100CC5064F`
- Snapshot 파일: 310개. 포함 파일의 내용 hash와 크기는 manifest를 따른다.
- Baseline: snapshot 안 `outputs/perf5d_bvh/reference/`의 변경 전 준비 소스.
- 검토할 보고서: snapshot 안 `docs/reports/2026-10-19_perf5d-bvh-preparation.md`.

원본 CAD, `.bitsam`, Python 환경, frontend와 다른 출력은 제외했다. 따라서
이 묶음은 읽기 전용 근거 검토용이지 전체 실행 가능한 배포본이 아니다.
실행 결과의 source hash가 현재 snapshot과 일치하는지 별도 확인해야 한다.

## 권한과 자원

- 모드: `evidence_only`.
- 읽기: 위 snapshot, manifest, 이 위임서만. 변경 중인 checkout으로 대체하지 않는다.
- 쓰기: 없음. 결과는 메인에 메시지로 반환한다.
- 명령: 파일 읽기·검색, SHA-256, JSON parsing·수치 재계산만 허용한다.
- 금지: 제품 소스 import/실행, 테스트, CUDA preflight, benchmark, build, 서버
  API 호출, package 변경, Git 변경, nested delegation, 사용자 CAD 열기.
- 시간 범위: 최대 15분의 1회 검토. 끝내지 못한 항목은 명시하고 추가 실행하지 않는다.
- GPU/무거운 CPU 작업: 누구도 이번 임무에서 시작하지 않는다.
- 메인 병행 작업: 운영 수칙·양식 연결 및 snapshot 보호 절차 점검. 검토 대상
  제품 소스와 기존 실행 근거는 수정하지 않는다.

## 사전 판정 기준

수치 기준은 기존 PERF-5D 보고서의 주장을 확인하는 기준이다. 이번 검토에서
새 허용 오차를 만들거나 기존 실패를 통과로 바꾸지 않는다.

| 항목 | 확인할 기준 |
| --- | --- |
| 입력 동일성 | manifest와 모든 입력 hash 유지; 기존 결과와 실행 소스의 연결 확인 |
| 구조 보존 | 전후 tree 배열·순서 metadata·stack width 동일 주장에 근거 존재 |
| 이산 결과 | 보고된 Ray/hit 수 exact 및 변경 전/후 반복 비교 근거 확인 |
| Receiver | 절대/상대 `1e-12` 및 최대 32 ULP 비교의 범위·계산·실패 처리 검토 |
| 보조 집계 | 전체 32 ULP 미통과를 유지; 기존 `1e-9` 비교와 혼동하지 않음 |
| 성능 | 두 CAD의 준비 구간 중앙값 재계산; 최초 JIT 효과를 가속률로 쓰지 않음 |
| 실행 증거 | 기존 preflight 및 16개 run의 state·성공/시도·hybrid/fallback 기록 확인 |
| 회귀 / sanitizer | 133검사·11모듈, sanitizer 각 4검사와 오류 0 주장에 대응 로그 존재 |
| 코드 위험 | 준비·분할·캐시 변경의 명백한 결함, 빠진 경계 테스트를 정적 검토 |

위 항목은 보고서의 근거 검토에 필수다. 실제 CPU/GPU 재실행, 다른 seed의
수렴성, 전체 저장소 테스트, 모든 보조 집계의 엄격 반복성, LT/실측 정합,
CPU 탐색 개선과 wide BVH는 이번 임무 범위 밖이다.

## 제출

시작 시 지침·권한·hash 확인을 먼저 알린다. 종료 시 입력 hash를 다시 확인하고
항목별 PASS/FAIL/NEEDS_RUN/BLOCKED/OUT_OF_SCOPE, 근거 파일·줄 또는 JSON
필드, 발견 사항의 영향도와 재현 조건을 제출한다. 제품 오류와 증거 부족을
구분하고, 전체 제품 정확도나 현재 서버의 GPU 상태를 새로 보증하지 않는다.
메인이 핵심 발견을 직접 확인한 뒤 최종 보고서를 별도로 남긴다.
