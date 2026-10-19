# PERF5D-REVIEW-02: 검증 도구 보완 독립 검토

## 위임

- 사용자 승인: 방법 A, 독립 검증 담당 한 명, 모델·추론 강도 위임.
- 지정 모델: `gpt-6.1-sol / high`.
- 목적: 이전 검토에서 찾은 비교기·후보 실행 증거·버전 고정 문제를 보완한
  검증 도구가 잘못된 통과를 만들지 않는지 검토한다. 제품 가속 구현의
  추가 변경이나 전체 PERF-5 승인은 이번 임무가 아니다.
- 전달 경로: source checkout, `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`
  및 미커밋 파일. 원본은 `C:\Users\Administrator\Documents\TV leakage simulator main`.
- Snapshot: 위 원본 경로의
  `outputs/validation/2026-10-19-perf5d-review-02/snapshot`.
- Manifest: 같은 review-02 디렉터리의 `manifest.json`.
- Manifest SHA-256:
  `42f45a5872917004ffc3277a304be85ff053f85bf4ec95c21099963c58a50c80`.
- 범위: manifest의 Python 소스·테스트·설정·지침·이전 보고서와
  `outputs/perf5d_bvh/reference`의 변경 전 BVH 소스.
  의존성 환경과 CAD fixture는 포함하지 않은 읽기 전용 검토 묶음이다.
- 모드: `evidence_only`. 읽기·hash·단순 수치 재계산만 허용한다.
  snapshot 소스 실행/import, 테스트·GPU preflight·API·benchmark는 금지한다.
- 쓰기 권한: 없음. 발견 사항과 최종 보고는 서브 대화에 남긴다.
- 시간 상한: 15분, 하위 에이전트 금지. 시작/종료 manifest와 파일 hash를 확인한다.
- 메인 병행 작업: 동일 소스의 실제 CUDA preflight, TV 및 CASE2 재측정,
  회귀 검사. CPU/GPU 측정은 메인만 한 번에 하나씩 수행한다.
- 서버·환경·사용자 원본·Git 상태를 변경하지 않는다.

## 점검 단계와 사전 기준

1. 입력 고정·권한 확인. 지침과 hash 확인을 먼저 보고한다.
2. `perf4_accuracy.py`의 signed zero, subnormal, 양·음수 ULP 순서와
   이산 필드 exact 판정을 검사한다. 기존 허용 오차 완화는 불허한다.
3. `perf5d_evidence.py`의 후보 pair BVH 계약·실제 실행·fallback·누락 필드
   fail-closed 판정, 시작/종료 파일 추가·삭제·변경 탐지를 검사한다.
4. `benchmark_perf5d_bvh_preparation.py`의 전체 입력·tree·semantic payload
   보존, 전후 및 같은 모드 반복 비교, 실제 후보 실행 강제, 예외 처리,
   통과 범위와 미통과 보조 집계의 구분을 검사한다.
5. 대응 회귀 테스트의 빈틈과 현재 승계 가능한 증거를 정리하고 최종 hash를 확인한다.

Receiver 기준은 `(abs <= 1e-12 OR rel <= 1e-12) AND ULP <= 32`,
전체 physical 회귀 기준은 `(abs <= 1e-9 OR rel <= 1e-9) AND ULP <= 2**48`,
전체 strict 보조 집계는 32 ULP를 별도로 보고한다. 이전 strict 실패를
전체 통과로 바꾸면 안 된다. 이산 항목 exact와 tree byte-exact는 필수다.
TV Receiver 0 hit와 비영 수광 검증은 구분한다. LT·실측 정합은 범위 밖이다.

시작 메시지에는 지정 모델·추론 강도, 총 5단계, 시작 시각을 표시한다.
단계 변경 시 완료 항목 수·현재 작업·실측 경과 시간·갱신 시각만 보고한다.
예상 진행률이나 남은 시간을 만들지 않는다.

## 제출

전체 판정과 항목별 PASS/FAIL/NEEDS_RUN/BLOCKED를 구분한다.
결함은 심각도, 파일·행, 재현 가능한 조건, 영향, 권장 수정 순으로 작성한다.
새 GPU 실행을 하지 않았음을 명시하고 메인의 새 실행 증거가 필요한 항목은
NEEDS_RUN으로 남긴다. 발견 사항이 없더라도 실행 검증 완료로 표현하지 않는다.
