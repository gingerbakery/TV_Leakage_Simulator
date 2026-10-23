# PERF-5E 공유 canonical pass 및 안전한 축소 검토

## 결론

실험용 device adapter의 중복 BVH 재검산을 제거했다. 검증한 입력에서 기존
면 선택·거리·Receiver 결과·저장된 경로를 보존했다. **전체 canonical oracle를
제거한 것은 아니며, 제품 OptiX 가속 채택이나 추가 속도 향상 확정도 아니다.**

기준은 source checkout `codex/ux-project-batch@373659d1`의 미커밋 실험 코드다.
제품 `src/`, `frontend/`는 기준 커밋과 동일하다. 이번 변경은 tools/scripts/tests/docs뿐이다.

## 변경 내용

- v1: 모호한 후보를 canonical BVH로 재검산하고, 모든 행을 다시 full oracle로 검사.
- v2: 변경하지 않은 제품 canonical BVH를 전체 행에 한 번 실행해 공유.
  재검산 행은 그 결과를 유지하고, 다른 행은 refined 후보의 face exact 및
  distance bit-exact를 비교한다. 불일치·invalid·overflow는 반환 전 실패한다.
- Ray 수·반사 상한·광학 조건·FP64·기존 탐색 순서와 tie 정책은 바꾸지 않았다.
- scratch 배열 네 개를 제거했다. capacity당 25 bytes 절약하며, capacity 256에서
  53,560 → 47,160 bytes다. 이 값은 전체 VRAM 사용량이 아니다.
- 실험 계약은 `experimental_device_selective_replay_shared_oracle_v2`로 구분한다.

최종 수송 검사의 CUDA adapter 72 batch, 557,060행에서 중복 91,504행 재탐색을
제거했다. 종전 코드상 canonical 작업량 648,564 → 557,060행, 약 14.1% 감소다.
**행/launch 감소율은 전체 해석 시간 개선율이 아니다.** OptiX 및 전체 oracle,
외부 관찰·전송·증거 저장 비용은 여전히 존재한다.

## 채택하지 않은 축소 방법

`OptiX 후보 거리 + 1e-10`까지만 기존 BVH를 탐색하면 안전할 것이라는 가정을
실제 CPU와 GPU에서 반증했다. 한 leaf의 면 순서가 아래와 같을 때:

| Face | +Z Ray와의 거리 (mm) |
| --- | --- |
| 0 | 5 + 1.5e-10 |
| 1 | 5 + 0.75e-10 |
| 2 | 5 |

원래 탐색은 Face 2를 선택하지만 상한을 `5 + 1e-10`으로 제한하면 Face 1이
선택된다. 중간에 선택·거절된 면이 다음 근접면 판정에 영향을 주기 때문이다.
거리 차이는 매우 작아도 서로 다른 면 속성이 적용될 수 있으므로 기존 결과
보존 계약으로는 허용하지 않는다. 6가지 배치 × 3회 반복 × CPU/GPU의 36개
기록 중 같은 배치의 6개 기록에서 반례를 재현했다.

이것은 **미채택 최적화의 위험**이며 현재 제품 CPU/GPU 불일치를 새로 발견했다는
뜻은 아니다. 전체 oracle는 유지하고 이 shortcut은 제품에 넣지 않았다.

## 실행 및 결과

기존 공식 `run_web_gpu` 환경 `.venv-gpu`를 사용했다. RTX 3070, driver 591.59,
CUDA Toolkit 13.1, Python 3.13.3, NumPy 2.4.6, Numba 0.66.0이다. 새 설치 없음.
Production preflight는 available/strict_float64/kernel_executed/kernel_verified
모두 true, scope `production_ray_bvh`, contract `strict_float64_bvh_v1`이었다.

| 검사 | 결과 | 범위 |
| --- | --- | --- |
| 경계/반복/배열 크기 변경 | 18장면, 108기록, 18,738행; face/거리 bit 차이 0 | 256/256/256/17/0/256행, 다른 producer stream |
| 고장 주입/복구 | 7항목 통과 | 잘못된 후보/flag, 누락, 1 ULP 손상, overflow flag, launch 예외/복구 |
| 직접 원시 감사 | 2,964검사, 실패 0 | 고정 장면·비어 있지 않은 행·계약·반복·NPZ 검사 |
| CPU/GPU 수송 전후 | 32쌍, 64해석; 각 backend의 기존 결과와 exact | 4장면 × CPU/GPU × 최초+warm 3회, 8,193 primary |
| 수송 원시 감사 | 10,760검사, 실패 0 | 총 1,574,728 질의행, 각 Receiver·보조 집계·경로 보존 |
| 이전 adapter와 직접 비교 | 108기록의 원시 864필드 및 64해석 의미 결과 동일 | v1 queries-v4/transport-v2와 v2 최종 결과 비교 |
| 회귀 | 127테스트 통과, 40.225초 | Workspace/Accessory 저장·복원, GPU 재해석, bitsam/API 포함 |
| Compute Sanitizer memcheck | error 0 | 32행 경계 harness, 108기록, 7 fault/recovery; 감사 2,964검사 통과 |

원시 OptiX 후보는 기존 면 선택과 다른 54,108행이 있었지만 재검산·검증 후
face 및 distance-bit 불일치는 0이다. 이 수치는 기존 실험과 같으며 전체 CAD
후보 누락이 불가능하다는 증명은 아니다. memcheck의 overflow 검사는 flag
주입이고 실제 구조적 stack 고갈 재현과는 구분한다.

GPU 요청별 canonical 실행 증거(각 반복의 같은 count):

| 장면 / emitter | state | CUDA 성공/시도 | CPU hybrid | hard fallback |
| --- | --- | --- | --- | --- |
| Face direct / face | gpu_active | 2/2 | 0 | 0 |
| Stochastic / datum_plane | gpu_mixed | 1/1 | 3 | 0 |
| Trapped depth10 / datum_plane | gpu_mixed | 11/11 | 11 | 0 |
| TV corner / datum_plane | gpu_mixed | 4/4 | 18 | 0 |

Mixed 이유는 `gpu_cuda_hybrid_small_waves`, hybrid 실패는 0이다. 이 count는
제품 canonical 호출이며 추가 OptiX/adapter launch를 합친 값이 아니다.
실제 resident optical loop는 이 진단에서 사용하지 않았다. CPU 요청의 shadow도
관찰용 OptiX를 실행하므로 그 시간으로 제품 CPU 가속을 주장하지 않는다.

진단 wall time 초(first / warm 1 / warm 2 / warm 3):

| GPU 장면 | baseline | shadow: OptiX+검증+증거 저장 |
| --- | --- | --- |
| Face | 2.457 / .027 / .025 / .026 | 1.371 / .303 / .286 / .265 |
| Stochastic | 23.234 / .026 / .025 / .027 | .280 / .270 / .278 / .287 |
| Trapped | .148 / .139 / .127 / .134 | 1.480 / 1.566 / 1.522 / 1.483 |
| TV corner | .216 / .215 / .206 / .208 | 1.772 / 1.841 / 1.788 / 1.814 |

공유 JIT/cache와 전송·증거 비용, 유휴 독점 미확인 때문에 formal speed gate에
쓰지 않는다. 특히 최초 실행의 큰 차이를 가속률로 해석하면 안 된다.

## 독립 검토와 수정 이력

Kierkegaard(`gpt-6.1-sol / high`) 한 명이 고정 snapshot을 `evidence_only`로
검토했다. GPU/테스트 실행은 메인만 했다.

- R1은 shared-pass 소스에는 결함을 찾지 않았지만 감사기 두 곳을 FAIL 처리했다.
  v1 보고서의 v2 이름 바꾸기, 18개 장면을 임의 이름/모두 빈 입력으로 바꾸는
  검사를 막지 못했다. 메인이 확인 후 버전별 구현 SHA·실행 metadata·장면/행
  matrix 검사 및 회귀를 추가했다. 실행 보고서는 수정하지 않고 queries-v2와
  transport-v2를 새로 생성했다.
- R2는 위 두 결함의 해결과 원시 108 NPZ/18,738행을 독립 확인했다. 추가로
  수송의 GPU 사용 gate가 빈 batch도 count할 수 있음을 지적했다. 메인은
  양수 GPU 행 검사를 batch와 workload 양쪽에 넣고, 빈 batch 및 모두 빈
  GPU 수송 보고서의 거절 회귀를 추가했다. 최종 원시 감사를 다시 실행했다.
- R3는 마지막 감사기 delta를 소스/근거 범위에서 PASS 처리했고 잔여 P2 해결을
  확인했다(59초). R2의 경계 원시 데이터 독립 확인과 구분한다. R3에는 수송
  원시 데이터와 최종 127검사 전체가 없으므로 그 실행을 독립 재검증했다고
  하지 않는다. 전체 실행 재검증이나 LT 정합성으로 통과 범위를 확대하지 않는다.

R1/R2 입력은 종료 후 메인도 각각 16/127파일 hash·크기 불일치 0을 확인했다.
R3도 5파일 불일치 0을 확인하고 검증 에이전트를 종료했다. Snapshot manifest는
R1 `e3549efc301ade5a4be01e669d381e6b26d921dfe41d5f347a15c36aca097834`,
R2 `8e505b5a9db55c13c6b3336726c855536ae666217f4e144583326e268fb9d2e1`,
R3 `4c950e9e37d0d416402003adf830652aede7068d34ea139e4431672b0cea4bc4`다.
초기 테스트 추가 과정의 클래스 배치 오류 4건은 수정했고 최종 127검사에 포함했다.
v1 실행/감사 근거는 보존하되 최종 판정은 강화한 v2/v3 감사 결과를 사용한다.

## 근거와 인수인계

로컬 근거 루트: `outputs/validation/perf5e-shared-oracle/` (Git 제외).

- `queries-v2/report.json`: `d66e2031a7286bc92fb365083ea523861e7638abee6aefa4a8a4bf76685bd6f4`
- `transport-v2/report.json`: `4d9fe3a98d8d47b2a2b6e45c51e74cfdab70646d0bc23044b9f1b61afd7ccda1`
- `memcheck-queries-v1/report.json`: `c99a9d3481731ec49db7b33889d7dc6aa2ce5210918312e79278037727d69acd`
- 감사: `query-audit-v2.json`, `transport-audit-v3.json`, `memcheck-audit-v1.json`.
- 추가 근거: `cutoff-v1/report.json`, `v1-v2-comparison.json`, `regression-v3.log`,
  `memcheck-v1.log`, `review-r1/`, `review-r2/`, `review-r3/`.

구현 변경 시 auditor의 v2 고정 SHA를 무작정 갱신하지 말고 새 snapshot/검토를
거쳐야 한다. Hash/로그는 버전 연결 근거이지 악의적으로 조작된 실행의 암호학적
증명은 아니다. GPU kernel/source·SDK 전체의 독립 재실행도 이번 reviewer 범위 밖이다.

Workspace/Batch 인수인계 파일과 겹친 제품 수정 없음. `.bitsam` schema,
Case별 결과 연결·취소, Accessory 처리, Ray Path 형식 변경 없음. Frontend 미변경으로
새 UI build는 하지 않았다. 서버·사용자 원본·다른 checkout·환경을 건드리지 않았고
commit/push도 하지 않았다.

다음 gate는 전체 oracle를 안전하게 없앨 수 있는 후보 누락 방지·기존 선택 순서
보존 설계다. 그 근거 없이 full oracle를 끄지 않는다. 실제 resident loop 연결,
대표 CAD 전체 시간 20% 이상 개선/작은 장면 3% 이내 회귀는 아직 미검증이다.
이번 단계는 결과 보존을 유지한 실험 경로 정리까지이며 PERF-5 전체 완료가 아니다.
