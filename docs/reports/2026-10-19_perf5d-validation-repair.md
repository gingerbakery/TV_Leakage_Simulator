# PERF-5D 검증 도구 보완과 고정 버전 재검증

## 결론과 범위

현재 5D의 보조 BVH 준비 개선분을 고정 소스로 다시 측정했다.
**선언된 구조·이산·Receiver·physical 회귀 기준은 통과했다.**
전체 보조 집계의 엄격한 32 ULP 반복성은 여전히 미통과이며, PERF-5 전체나
LT·실측 정합을 완료했다는 보고가 아니다.

- TV 보조 BVH 준비 중앙값: 2.743594 → 1.617045초, **41.1% 단축**.
- CASE2 준비 중앙값: 0.199174 → 0.129647초, **34.9% 단축**.
- 준비된 형상을 재사용하는 전체 추적 시간은 유지된다. 위 수치는 전체
  Ray Tracing이나 CAD import의 추가 가속률이 아니다.
- CASE2의 Peak/Mean 추정 nit, Flux 및 공개 Receiver 지표는 8회 모두 같은 값이다.
  Heatmap cell은 최대 1 ULP, `6.776263578034403e-21 lm` 차이다.
- 메인이 새로 실행한 14모듈 **160검사 통과**, 실패·오류·skip 0.
  실제 GPU sanitizer 3종도 각각 4검사와 오류·경합 0을 확인했다.

이번 작업에서는 검증 스크립트·테스트·운영 문서를 수정했다. REVIEW-02 이후
제품 `src/` 49파일의 hash는 모두 그대로다. 광원, 난수, 교차·반사 계산,
면 속성, 종료 정책, 광량 누적, nit 환산을 추가로 변경하지 않았다.

## 수정한 검증상의 문제

1. **0 부근 ULP 비교 오류:** 음수·양수 경계를 단조 순서로 매핑하고
   signed zero는 같은 값으로 취급한다. `-5e-324 → 0` 거리는 1이다.
   허용 오차는 바꾸지 않았고 비교기 계약만 v2로 구분한다.
2. **후보 미실행의 거짓 통과 방지:** GPU 활성뿐 아니라 pair BVH 계약,
   실제 선택 batch, CUDA/resident 시도·성공 일치, hybrid/fallback 0을
   모두 요구한다. 누락·잘못된 타입·0 성공·일반 GPU 경로는 거부한다.
3. **같은 모드 반복 gate 추가:** before 최초 대비 비교 외에 각 모드의
   최초 실행 대비 Receiver strict와 전체 physical 반복 판정도 필수화했다.
   공통 기준의 양쪽에 있는 값끼리는 허용 범위를 초과할 수 있는 반례를 추가했다.
4. **변경 전 기준 인증:** 파일이 실행 중 바뀌지 않았다는 사실과 승인된
   baseline이라는 사실을 구분한다. 두 pre-5D 파일의 고정 SHA-256을
   인증하며 현재 후보를 baseline으로 잘못 지정하면 실행을 거부한다.
5. **재계산 가능한 근거:** API·검증 스크립트·테스트·설정까지 시작/종료
   hash를 남기고, 전체 입력·tree 배열·semantic payload를 로컬에 보존한다.
   실행별 performance/gate를 즉시 저장하고, warm tree 불일치 배열과
   후속 예외 이전의 결과도 남긴다. 최종 benchmark 계약은 v3다.
6. **경계·실패 회귀:** traceable face 4,095/4,096/4,097, Trace Off와
   캐시 무효화, 잘못된 기준 버전, 뒤늦은 예외, 실제 mesh metadata
   직렬화를 검사한다. 관련 좁은 범위 33검사는 위 160검사에 포함된다.

수정 중 REVIEW-03 실행은 존재하지 않는 mesh 제외면 속성을 참조해 입력
저장 단계에서 중단됐다. 가속 추적 결과가 아니라 검증 도구의 오류였다.
실제 `face_metadata.trace_excluded` 보존 방식으로 수정하고 회귀 검사를
추가했다. 이 실패와 초기 재측정은 삭제하지 않았고, 최종 판정에는
`rerun-04`만 사용한다.

## 고정 입력과 실행 환경

- Delivery: source checkout,
  `main@157230c68dabaf5dca1f82b1767793f9ef835a0a` + 미커밋 PERF 코드.
- Repository: `C:\Users\Administrator\Documents\TV leakage simulator main`.
  별도 rendering checkout에는 손대지 않았다.
- 기존 GPU launcher가 준비한 `.venv-gpu`, Python 3.13.3 x64,
  NumPy 2.4.6, Numba 0.66.0, llvmlite 0.48.0.
- NVIDIA GeForce RTX 3070, compute capability 8.6, driver 591.59,
  CUDA Toolkit 13.1. Compute Sanitizer 2025.4.1.0.
- 비교 기준: Git의 구버전 전체 제품이 아니라 **5D 준비 최적화 직전의
  로컬 SAH 소스**. before/after 모두 현재 수송·누적 경로를 사용한다.
- 승인 baseline:
  `gpu_cuda_pair_scene.py=ae63a5b297c1d64166b1998e0aecab47e5511d8d10a96ed8537fa450110ee6c1`,
  `gpu_cuda_pair_sah.py=0e6152f31f9ff9cd7de70989e63de9ff14271ad8d0ddb994214576352db74227`.
- 최종 소스 검토 snapshot: `outputs/validation/2026-10-19-perf5d-review-04/`.
  Manifest SHA-256:
  `3eb87abd169c80401ebedf59a391b0f6621137f1207617515277fe54586f3cb3`.
- Snapshot 302파일이 유지됐고, 실행 관련 291파일은 두 benchmark와 회귀·
  sanitizer 실행 전후 모두 일치한다. 원본 입력 파일도 hash가 유지됐다.

| 조건 | TV 코너 | 사용자 CASE2 |
| --- | ---: | ---: |
| 삼각형 | 50,944 | 4,106 |
| Ray 수 / seed | 1,000,000 / 42 | 1,000,000 / 42 |
| 반사 상한 | 10 | 100 |
| 광원 | Datum Plane / Lambertian | Datum Plane / Aim Area |
| Receiver | 40 × 24 cells | 5 × 3 mm / 25 × 15 cells |
| 면 속성 | Mixed, 반사율 60% | 저장된 profile·면별 지정 유지 |

CASE2의 `aim.enabled=true`, `mode=area`, `distribution=uniform_target_area`를
입력 원문으로 확인했다. 기본 `direction_distribution=lambertian` 필드만
보고 Aim을 꺼진 것으로 해석하지 않는다. 원본 5,000만 Ray 설정에서 검증
Ray 수만 100만으로 바꾼 것이며, 저장된 5,000만 Ray 결과 재현 검사는 아니다.
원본 `.bitsam` SHA-256은
`61f5e0d6153adeed7439eebf70fdc3a283ea98723d69c5d7b931c64dc323b12d`다.

동일 장면·동일 프로세스에서 before/after 순서를 교대하고 최초와 warm 3회를
구분했다. 서버 프로세스의 유휴 상태를 확인한 뒤 메인만 GPU 검사를 직렬
실행했다. 일반 desktop/browser는 유지했으므로 1% 미만의 차이를 유의한
속도 향상 또는 저하로 판정하지 않는다.

## 성능과 실제 GPU 실행

| 구간 / warm 중앙값 | TV before → after | CASE2 before → after |
| --- | ---: | ---: |
| 보조 BVH 준비 | 2.743594 → 1.617045초 | 0.199174 → 0.129647초 |
| 준비 감소율 | 41.1% | 34.9% |
| 캐시 사용 전체 추적 | 0.727804 → 0.732070초 | 1.543674 → 1.554039초 |

최초 추적은 TV 14.155486→2.607002초, CASE2 17.000387→1.764153초이나
공통 CUDA JIT 및 실행 순서 효과가 있으므로 이 비율을 가속률로 쓰지 않는다.
배열 크기는 각각 16,776,704 / 1,271,602 bytes로 before/after가 같다.

두 benchmark의 production preflight에서 `available`, `strict_float64`,
`kernel_executed`, `kernel_verified`는 모두 true이며,
`preflight_scope=production_ray_bvh`,
`provider_contract=strict_float64_bvh_v1`이다.
총 16회 모두 `compute_execution_state=gpu_active`, reason null,
CUDA 성공/시도 16/16, resident 성공/시도 16/16, pair 선택 16이다.
CPU hybrid와 resident/intersection/planner/reducer fallback은 모두 0이다.

## 정확도와 반복성

사전 허용 기준은 그대로 유지했다.

- Tree: 11개 배열의 dtype·shape·bytes와 stack width exact.
- 이산 결과: exact.
- Receiver: `(abs <= 1e-12 OR rel <= 1e-12) AND ULP <= 32`.
- 전체 physical: `(abs <= 1e-9 OR rel <= 1e-9) AND ULP <= 2**48`.
- 같은 모드 최초 실행 대비 반복도 Receiver/physical 기준을 각각 적용한다.
- 전체 보조 집계까지 32 ULP를 적용한 별도 엄격 결과는 숨기지 않는다.

16회의 필수 gate가 모두 통과했다. 메인은 저장된 NPZ의 hash와 bytes,
전체 payload·실행별 checkpoint, 비교 gate를 다시 계산했다. 이는 기록의
재현 검증이며 독립된 광학 정답 계산기는 아니다.

CASE2의 Receiver hit는 모두 **219**, surface hit는 모두 **5,527,604**다.
공개 Receiver 지표는 모두 같은 값이며 Peak nit_est 111.97878684705327,
Mean nit_est 3.6148195808740398, Flux 0.0014195363299158022 lm이다.
Heatmap 차이는 전후·같은 모드 반복 모두 최대 1 ULP다.

**남은 한계:**

- 전체 보조 집계 32 ULP 기준은 전후 비교 14/16회, 같은 모드 반복 12/16회에서
  미통과다. before 자체 반복에도 있다. 기여도 lobe/depth와 일부 optical
  profile 누적 필드가 포함된다. 수정 전후 비교 최대 절대 차이는 TV
  `9.636735853746359e-14`, CASE2 `2.2026824808563106e-12`이며,
  CASE2 같은 모드 반복 최대는 `2.6147972675971687e-12`다.
  이 차이가 physical gate 이내라는 사실과 엄격 반복성 실패는 별개로 남긴다.
- TV는 모든 실행에서 0 Receiver hit다. 비영 수광량 정확성 증거가 아니다.
- CASE2는 0.584 hit/cell, peak 유효 표본 약 4.77로 희소하다. 동일 입력
  구현 간 보존 검사는 가능하지만 peak의 통계적 수렴이나 LT 정합을 입증하지 않는다.
- GPU 업로드 뒤 반복 geometry edit의 장기 메모리 회수, 다중 seed 수렴,
  전체 저장소 테스트, CPU 탐색 구조와 wide BVH는 이번 통과 범위 밖이다.

## 실행 회귀·GPU 안전성

메인이 실행한 160검사에는 비교기, 기록 보존, 4,096면 경계, pair 탐색,
native CPU 수송, optical routing, bridge, geometry/optical audit가 포함된다.
실제 1,000회 반사, 취소·재실행·실패 복구, 미소 gap, ROI custom 물성,
면 속성 재지정, CPU 경로 분리도 해당 검사 범위에서 확인했다.

| 메인 새 실행 | 결과 |
| --- | --- |
| 14모듈 unittest | 160 통과 / 실패·오류·skip 0 |
| Compute Sanitizer memcheck | 4 통과 / 오류 0 |
| Compute Sanitizer synccheck | 4 통과 / 오류 0 |
| Compute Sanitizer racecheck | 4 통과 / hazard·오류·경고 0 |

Sanitizer는 위 주요 GPU 검사 4개를 각 도구로 반복한 것이며 12개의 별개
제품 시나리오라는 뜻은 아니다. 전체 명령, 도구 버전, source hash 전후,
exit code와 원문 로그를 함께 보존했다.

## 독립 검토와 통합 판단

검증 담당 Dalton은 지정 모델 `gpt-6.1-sol / high`로 제품 파일을 수정하거나
GPU를 사용하지 않고 검토했다. REVIEW-02에서 같은 모드 반복 gate,
baseline 인증, 실패 근거 보존의 3건을 추가로 발견했고 메인이 수정했다.
REVIEW-04에서 고정 TV/CASE2 기록과 선언된 gate를 PASS로 판단했고
새 actionable 불일치는 없었다. Snapshot 302파일 및 별도 근거 3파일의
종료 hash 유지도 확인했다.

검증 담당에게 전달된 범위에는 완료된 160회귀·sanitizer 로그가 없어서 그
항목은 NEEDS_RUN으로 남겼다. 위 회귀·안전성 통과는 **메인이 실제 실행하고
대조한 결과**이지, 서브 에이전트가 실행하거나 승인한 결과로 표현하지 않는다.
전체32ULP·통계적 수렴·LT 정합의 미검증/실패는 양쪽 모두 유지한다.

## 근거와 다음 단계

최종 로컬 근거는 `outputs/validation/2026-10-19-perf5d-rerun-04/`에 있다.
`tv.json`, `case2.json`, 각 `.artifacts/`, `artifact-replay.json`,
`regression.json`, `verification-suite.json`, `final-integrity.json` 및
원문 로그를 보존한다. 사용자 형상·큰 근거는 Git 제외 경로에만 저장했다.

다음 5D 단계는 CPU 탐색 구조·leaf/layout 후보를 현재 고정 결과와 비교하는
작업이다. 이번 Receiver/physical/반복 gate와 면 ID·미소 gap 조건을 그대로
사용하며, 전체 보조 집계의 엄격 반복성은 별도 이슈로 추적한다.
wide BVH나 OptiX를 검증 없이 기본 경로로 바꾸지 않는다.

서버 8788·8789와 사용자 세션은 유지했다. 설치·서버 재시작·fetch/pull·
commit·push는 수행하지 않았다. 기존 최소 2배 목표도 자동 재개하지 않았다.
