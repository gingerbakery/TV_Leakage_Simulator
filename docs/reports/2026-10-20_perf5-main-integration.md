# PERF-5 가속 변경과 최신 main 통합 검증

## 판정과 범위

2026-10-20, 사내 사용자 비교 테스트용으로 배포할 수 있는 상태까지 통합했다.
CPU/GPU 계산, BSDF, 프로젝트 저장·복원 후 재해석, 최신 UI를 검사했다.
이 보고서는 LT 또는 실측 nit 정합 완료, 모든 모델의 무오류, 모든 워크로드의
가속률을 보장하지 않는다. PERF-5D 전체 로드맵이 완료됐다는 뜻도 아니다.

## 통합 이력

| 항목 | 커밋 |
| --- | --- |
| 가속 개발 시작 기준 | `157230c6` |
| 미커밋 PERF-5 작업 체크포인트 | `1abafc19` |
| 첫 최신 main 반영 | `f042626f` → `fa6269c4` |
| BSDF 전송·저장 복원 검사 보강 | `4cb3ce1e` |
| 검증 도구 호환성 보완 | `7ce9a9f6` |
| 검증 중 추가된 main 반영 | `7124f9e1` → `08155eee` |

원격의 BSDF import/transport, body/face emitter 편집, 방향 표시, 저장 경로
표시 개수와 선택 영역 기여도 분석을 보존했다. 마지막 `7124f9e1`은
frontend만 변경하며 검사 중인 backend 계산 소스는 변경하지 않았다.
결과 UI의 추가 설명은 기존 `(?)` 도움말로 옮겼다. 대량 경로 저장에 따른
메모리·전송 시간 경고는 사용자 보호를 위해 계속 표시한다.

## 충돌 조정과 실제 계산 수정

- CPU 병렬 wavefront에서 BSDF material index도 Ray 구간과 함께 잘라
  전달한다. BSDF는 표를 해석하지 않는 analytic-only fused 경로로 보내지
  않고 BSDF-aware counter 경로를 사용한다.
- 광학 palette에서 BSDF profile을 analytic scatter code로 잘못 캐시하지
  않도록 구분한다. 재질 수정과 재실행에서도 갱신한다.
- CUDA resident 경로는 가속용 packed binding과 BSDF 표 5개를 함께
  전달한다. 기존 pair BVH, batch 재시도와 event overflow 처리를 보존한다.
- **실제 통합 오류를 수정했다.** GPU global Receiver 집계가 3개 반사
  lobe를 가정해 새 BSDF lobe의 수광량을 누락했다. Receiver별 grid와
  global BSDF 집계가 불일치했으며, `LOBE_COUNT` 기반 offset으로 통일했다.
  CPU BSDF 수광량 0.3 lm에 대해 GPU global 값이 0이던 재현을 해소했다.
- 4 lobe / 7 Receiver field 계약에 맞춰 누적기·전송·별칭 검사를 보강했다.
  허용오차나 물리 모델을 완화해 통과시킨 변경은 없다.

## 실행 환경

- 전달 경로: Git source checkout, `main`. 다른 rendering worktree는 수정하지 않았다.
- 공식 `run_web_gpu.ps1 -PreflightOnly`로 환경 동기화와 초기 빌드 및
  production CUDA 검사를 실행했다. 서버는 시작하거나 재시작하지 않았다.
- NVIDIA GeForce RTX 3070, driver 591.59, CUDA Toolkit 13.1, compute capability 8.6.
- Python 3.13.3 x64, NumPy 2.4.6, Numba 0.66.0, llvmlite 0.48.0.
- `available`, `strict_float64`, `kernel_executed`, `kernel_verified`: 모두 true.
- `preflight_scope=production_ray_bvh`, `provider_contract=strict_float64_bvh_v1`.
- 테스트·GPU 검사·UI 빌드는 직렬로 실행했다. 사용자 서버, 원본 CAD,
  원본 `.bitsam`, 브라우저 세션, OS driver와 시스템 설정은 변경하지 않았다.

## 회귀 검사 결과

근거 파일은 로컬 `outputs/validation/2026-10-20-perf5-main-integration/`에
보관한다. 실패 로그도 삭제하지 않았다.

| 검사 | 결과 / 근거 |
| --- | --- |
| BSDF/누적기 집중 검사 | 98건 통과, `targeted-03.json` |
| 전송·workspace CPU 검사 | 23건 통과, `static-fixtures.log` |
| 광범위 backend 회귀 | 845건 실행, 최초 1 failure / 25 error, `full-regression-02.json` |
| 문제 모듈 및 추가 5D 검사 | 69건 통과, failure/error/skip 0, `repaired-regression.json` |
| 누적 근거 동일성 검사 | 통과, `cumulative-regression.json` |
| CPU/GPU BSDF 및 독립 적분 기준 | 14조건 통과, `bsdf-gpu/report.json` |
| NVIDIA memcheck | 4건 통과, ERROR SUMMARY 0, `sanitizer-tests.log`, `memcheck-14216.log` |
| Frontend 전체 Vitest | 53파일 / 472건 통과, `frontend-tests.log` |
| Frontend lint | 종료 코드 0, 기존 경고 7건, `frontend-lint.log` |
| TypeScript 및 production UI build | 통과, `frontend-build.log` |

### 최초 광범위 검사 실패와 재검증의 의미

예정한 856건 중 BSDF import 모듈 12건이 runner의 root import path 누락으로
로딩되지 않아, 실패 placeholder 1건을 포함한 845건이 실행됐다. 나머지
실패는 BSDF code 5를 미지원으로 가정하던 검사 1건, 이전 BVH 생성 함수의
코드 모양을 전제로 한 **실험용** leaf 진단 스크립트 24건이었다.

제품 계산 코드는 바꾸지 않고 다음 검증 코드만 보완했다.

- BSDF code 5는 표/indices가 필요하다는 검사를 유지하고, 미지원 code 검사는 6으로 구분.
- root import path를 runner에 추가해 BSDF import 검사를 실제로 실행.
- 실험용 leaf builder의 AST 변환을 현재 생성 함수에 맞추고 여러 삼각형의
  경계를 모두 감싸는지 검사 추가. 해당 실험을 제품 기본 경로로 활성화하지 않았다.

문제 모듈 전체 44건과 별도 PERF-5D 25건을 다시 실행해 69건 모두 통과했다.
주 회귀 856조건의 판정은 **최초 실행 + 해당 모듈 재실행의 누적 근거**다.
856건 전체가 한 번의 깨끗한 재실행에서 통과했다고 표현하지 않는다.
두 실행과 현재의 제품 소스 hash가 같고, 변경한 테스트·진단 스크립트와
실패 모듈의 재실행 범위가 일치함을 `cumulative-regression.json`에서 확인했다.
진행 중 중단한 더 이른 실행은 `full-regression-interrupted.json`에 별도로
보존했으며 통과 근거에 포함하지 않았다.

## CPU/GPU 결과와 저장·불러오기

BSDF 검사는 Lambertian/방향성 분포, 0~80° 입사, 반사율 0/1,
다회반사와 roulette, 상세 기여도, 두 BSDF 재질, Face 광원, 파장/입사각
보간, chunk 분할을 포함한다. 기본 6조건은 각각 100,000 Ray,
추가 8조건은 각각 20,000 Ray다.

- 28번의 GPU 실행 모두 `gpu_active`, reason null.
- CUDA 성공/시도 합계 **52/52**, resident 성공 합계 52.
- CPU hybrid success, intersection/resident/planner/reducer fallback: 모두 0.
- 광원 유형: `datum_plane`, `face`. CPU/GPU 동일 Monte Carlo 계약 확인.
- 모든 이산 결과 일치, 물리량 절대/상대 `1e-12` 기준 통과.
- 별도로 남긴 엄격 ULP 감사는 28실행 중 5개에서 미통과했다.
  near-zero 좌표 및 누적 반올림 차이까지 bit-exact라고 주장하지 않는다.
- 독립 적분 대비 최대 수광량 오차 **0.182681%**, 최소 상관 **0.999579**,
  최소 분포 겹침 **98.2248%**. 사전 기준 1% / 0.99 / 95%를 충족했다.

동일 세션 첫 실행과 warm 2회도 기록했다. Lambertian 100k는 GPU
10.911 / 0.0504 / 0.0509초, 방향성 60°는 0.0560 / 0.0547 / 0.0650초였다.
작은 합성 장면과 JIT 비용의 기록이며 TV 조립체 전체의 가속률로 환산하지 않는다.
이번 통합에서 사용자의 50M CASE2 속도 비교를 다시 실행하지는 않았다.

Portable 검사는 CPU와 GPU 각각 결과 grid, BSDF asset, 면 속성 링크,
Emitter/Receiver/config를 복원한다. 원래 메모리 request를 재사용하지 않고
**불러온 workspace로 새 request를 만들어** 재해석하며 Receiver 크기 변경도
검사한다. Frontend에서도 새 store에 저장 상태를 복원한 뒤 API request를
재구성한다. 이 증거는 API/store 자동 검사이며 사용자의 실제 브라우저 파일
선택부터 전체 GUI를 조작한 end-to-end 검사로 표현하지 않는다.

## 독립 검토와 남은 범위

검증 전용 reviewer(Jason, gpt-6.1-sol / high)가 고정 snapshot을 읽기 전용으로
검토했다. 제품 수정·실행·GPU 측정·Git 작업은 메인 담당자가 수행했다.
최종 검토 snapshot 307파일의 manifest SHA-256은
`9f096539df075cf3bbd268d59380c099188b7f2777eba01b552e67e9cb9da4a7`이다.
검토 전후 hash가 일치하며 BSDF 전송 fixture와 복원 request 관련 지적은 해결됐다.
Reviewer의 정적 검토 통과와 이후 메인이 수행한 실제 장치 검사 결과를 구분한다.

- LT/실측 광량 및 nit 교차 검증은 미실행이다.
- 전체 GPU 보조 집계의 bit 결정성, 모든 모델의 Peak 통계 수렴은 보장하지 않는다.
- Frontend lint의 기존 hook 의존성/정규식 경고 7건, Vite 큰 bundle 경고는
  유지된다. jsdom canvas 미구현 경고도 있어 실제 픽셀 렌더링 검증과 구분한다.
- 최신 main의 최대 저장 경로 50,000개는 진단 옵션이다. 크게 늘리면 결과
  정리·전송·저장 시간이 증가하므로 속도 비교 때 기존 값과 동일하게 맞춰야 한다.
- GPU ZIP/EXE는 이번에 만들지 않았다. 다른 PC는 pull 후 공식
  `run_web_gpu.bat`으로 환경·최신 frontend를 동기화하고 실행한다.
- 사용자 CAD/해석 파일, 로컬 검토 메모, 두 개의 로컬 저장 진단 파일은
  커밋 대상에서 제외했다. 실행 중 서버를 최신 backend로 바꾸려면 작업 저장 후
  별도로 재시작해야 한다.
