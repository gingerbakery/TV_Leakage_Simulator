# PERF-5E — GPU device-buffer 선택적 재검산

## 결론과 적용 범위

`codex/ux-project-batch@373659d16bfb58190f8790c89ff7d71114acc887` 위의
미커밋 실험 도구다. 제품 `src/`, `frontend/`는 변경하지 않았다.
OptiX 후보 분류, 기존 FP64 거리 재계산, 애매한 행의 canonical BVH 재탐색,
전체 canonical oracle 비교를 GPU 버퍼 안에서 연결했다.

**제품 가속 채택 또는 PERF-5E 완료 보고서가 아니다.** 후보 누락의 전역적
보수성은 아직 입증되지 않아 모든 행의 전체 oracle를 계속 실행한다.
제품의 fused/resident 광학 수송에 연결하지 않았으며 속도 gate도 미실행이다.
결과 보존 검증은 물리 정답·LT 정합을 증명하지 않는다.

## 구현

- `tools/perf5e_native/device_replay.py`: 고정 용량 GPU workspace, typed Numba
  입력, 명시적 stream 의존성, 후보 분류·FP64 refinement·재탐색·bit 검사.
- 후보가 확실해 보이는 행은 재탐색 구간만 비워 기존 BVH kernel의 traversal을
  생략한다. kernel launch 자체는 전체 행에 수행된다. 순서에 민감한 기존
  tie 판정을 새로 구현하거나 가까운 면들을 임의로 합치지 않았다.
- miss/guard/근접·경계 후보와 refinement 실패는 기존 BVH로 재검산한다.
  유효하지 않은 후보, overflow, face 또는 거리 bit 불일치는 결과 반환 전에
  실패한다. 전체 oracle를 생략하는 옵션은 없다.
- adapter 내부 ray upload/hit download는 0이며, query마다 56-byte 상태값을
  내려받는다. native launch parameter 전달과 동기화는 여전히 필요하다.
  scene 준비/초기 upload는 이 per-query 수치에 포함하지 않는다.
- 반환 device view는 다음 query/close까지만 유효하다. 다른 thread/context,
  용량 초과, 닫힌 객체는 거부한다. 형상 변경 시 adapter를 새로 만들어야 한다.
  enqueue 후 예외에도 stream 완료를 기다린 뒤 예외를 다시 전달한다.
- `device_transport.py`와 `verify_perf5e_transport.py --device-replay`는 별도
  프로세스의 host-roundtrip 진단 수송에만 연결한다. CUDA batch는 새 adapter,
  작은 CPU hybrid batch는 기존 CPU 재검산을 사용한다. **이 진단 harness의
  입출력과 원시 증거 저장에는 여전히 host 전송이 있다.**

## 실행 환경

전달 형태는 source checkout이며 기존 `run_web_gpu`로 준비된 `.venv-gpu`를
사용했다. 설치·서버 변경은 하지 않았다. RTX 3070 / driver 591.59,
CUDA 13.1, OptiX SDK 9.1.0, Python 3.13.3, NumPy 2.4.6,
Numba 0.66.0 / llvmlite 0.48.0이다.

각 GPU 검증의 production preflight에서 `available`, `strict_float64`,
`kernel_executed`, `kernel_verified`가 모두 true였고 scope는
`production_ray_bvh`, contract는 `strict_float64_bvh_v1`이었다.
정식 속도 측정용 GPU 독점 확인은 이번에 받지 않았으며 모든 시간은 진단용이다.
native 정상 build는 `outputs/validation/perf5e-device/native-v4`다.

## 검증 결과

원시 근거는 Git 제외 경로 `outputs/validation/perf5e-device/`에 보존했다.

| 검사 | 결과 | 제한 |
| --- | --- | --- |
| Device query 경계·반복 | 18조건 × 6회 = 108기록, 18,738행 exact | 유한 fixture, 전역 coverage 아님 |
| 후보 처리 | replay 8,573 / winner-only 10,165, 최종 face·거리 bit 차이 0 | full oracle 18,738행도 별도 실행 |
| Fault injection | 5검사 통과 | 실제 hardware fault/stack overflow 주입은 아님 |
| 다회반사 수송 | 4장면 × CPU/GPU × first+3warm = 32쌍, 64해석 | host-roundtrip, primary 8,193, stored path 32 |
| 수송 결과 | baseline/candidate 및 같은 모드 반복 semantic exact | 다른 seed/LT/실측은 이번 범위 밖 |
| 원시 수송 감사 | 8,928검사, 실패 0 | dtype/shape 확인 후 NPZ/JSON 재계산 |
| Device 원시 감사 | 2,053검사, 실패 0 | 제품 속도·물리 정답 판정 아님 |
| 회귀 | 112 unittest 통과, 40.603초 | Workspace/Accessory 저장·복원, GPU 재해석, API 포함 |
| Compute Sanitizer memcheck | 32-ray fixture의 108기록, 0 errors | racecheck/synccheck는 이번에 실행하지 않음 |
| 컴파일·정적 확인 | compileall, git diff --check 통과 | UI 변경·브라우저 검사는 없음 |

Device query는 각 조건에서 256행 first+2warm 뒤 17→0→256행을 실행한다.
다른 producer stream의 입력, workspace pointer 재사용, 입력 불변, 닫힌 객체
거부를 확인했다. Fault는 틀린 unflagged 먼 면, 알 수 없는 flags, 모든 후보
누락, classify enqueue 뒤 launch 실패 정리, 실패 후 동일 workspace 회복이다.
후보 전체 누락은 ray를 버리지 않고 모두 canonical로 재탐색했다.

수송에서 새 device adapter를 실제 사용한 것은 72 batch / 557,060행이다.
이 중 replay 91,504행, winner-only 465,556행이며 전체 GPU oracle도 557,060행
실행했다. CPU hybrid까지 합친 모든 진단 query는 1,574,728행이다.
raw OptiX face 차이 54,108행은 숨기지 않고 남겼고 재검산 후 face/거리 bit
차이는 0이었다. 이 수치로 속도 향상을 계산하지 않는다.

### 실제 CUDA 실행 기록

아래는 각 GPU 수송 run의 기존 canonical dispatch 카운터다. adapter의 추가
OptiX/refinement/oracle launch는 이 카운터에 합산하지 않으며 별도로 기록했다.
CPU-request 진단도 OptiX 관찰에는 GPU를 사용하므로 제품 CPU 가속 측정이 아니다.

| 장면 | state | CUDA 성공/시도 | CPU hybrid 성공 | 이유 |
| --- | --- | --- | --- | --- |
| Face direct | gpu_active | 2/2 | 0 | 없음 |
| Stochastic two bounce | gpu_mixed | 1/1 | 3 | gpu_cuda_hybrid_small_waves |
| Trapped corridor depth10 | gpu_mixed | 11/11 | 11 | gpu_cuda_hybrid_small_waves |
| TV corner | gpu_mixed | 4/4 | 18 | gpu_cuda_hybrid_small_waves |

hard fallback과 resident fallback은 0이다. 여기서는 resident 수송 자체를
선택하지 않아 resident success도 0이다. 이를 full-resident 성공으로 부르지 않는다.
Emitter 종류는 각 `transport-v2/report.json` record에 보존했다.

GPU 진단 전체 wall time(first / warm 1 / warm 2 / warm 3, 초):

| 장면 | baseline | candidate + oracle + 증거 저장 |
| --- | --- | --- |
| Face direct | 2.46 / 0.02 / 0.02 / 0.02 | 1.30 / 0.23 / 0.25 / 0.25 |
| Stochastic two bounce | 22.61 / 0.03 / 0.03 / 0.03 | 0.34 / 0.33 / 0.35 / 0.32 |
| Trapped corridor | 0.14 / 0.13 / 0.13 / 0.13 | 1.50 / 1.46 / 1.48 / 1.47 |
| TV corner | 0.23 / 0.22 / 0.21 / 0.16 | 1.83 / 1.81 / 1.85 / 1.77 |

공유 JIT/cache, 이중 oracle, coverage 관찰, 증거 저장이 포함된 시간이다.
first 숫자나 이 표의 비율을 제품 가속률로 사용하지 않는다.

## 발견·수정 이력과 독립 검토

검증 담당 Peirce(`gpt-6.1-sol / high`)는 고정 SHA-256 snapshot을 읽기 전용
검토했다. 메인이 모든 테스트/GPU 실행을 담당했고 검증 담당은 실행하지 않았다.
새 snapshot마다 수칙·identity·허용 범위 확인을 요구했고 한 명만 운영했다.

1. R3: 원시 감사기의 상위 shape만으로는 구조화 subarray 및 dtype 변경을
   막지 못함을 지적했다. canonical dtype/shape를 연산 전에 비교하고
   float face, uint64 distance, subarray broadcasting 등의 회귀를 추가했다.
   이전 `transport-v2` 원시 자료도 다시 감사해 8,928검사/실패0이었다.
2. 새 adapter 첫 실행은 Numba `as_cuda_array` 변환에서 structured dtype이
   `V72`로 사라져 컴파일 실패했다(`queries-v1`). typed Numba device array로
   입력 계약을 좁히고 불필요한 변환을 제거했다. 실패 로그는 보존했다.
3. R4: enqueue 뒤 예외가 발생할 때 stream 완료를 보장하지 않는 경로를
   지적했다. 같은 문제를 메인도 확인해 예외 동기화 및 fault/recovery 검사를
   추가했다. 수정 후 `queries-v4`, memcheck, `transport-v2`를 재실행했다.
4. R5: 최종 예외 정리 및 원시 감사기 delta는 정적 PASS였다. R4의 예외
   정리 결함 해결, dtype/shape·bit 검사 및 mandatory oracle 유지를 확인했다.
   검증 담당에게 raw NPZ/바이너리 전체를 제공하지 않아 독립 실행은
   NEEDS_RUN이며 메인의 32쌍 해석 실행에 독립 PASS를 적용하지 않는다.
   메인은 원시 감사를 별도로 실행하고 최종 snapshot hash도 직접 대조했다.
   memcheck 로그 자체에는 command/report SHA가 내장되지 않은 한계가 있다.
   메인 도구 실행 기록의 명령·exit 0과 report identity를 함께 보존했다.

R3/R4/R5 snapshot은 종료 시 각각 8/19/17개 파일의 hash·크기 불일치가 0이었다.
R5 검토는 20:44:02–20:47:06 KST, 3분 4초였고 이후 검증 에이전트를 종료했다.
발견된 결함은 실험 연결 및 검사 도구의 결함이며 새 제품 광학 계산 오류를
확인했다는 뜻은 아니다.

Snapshot manifest:

- R3: `75186943be03a14a7801bf687267eb9c026c55df5de20c0985c49e037969f7f4`
- R4: `02592aaed776e6cda52b66a249b199955eb1e5a132ccc53203129a6ee49ab9d9`
- R5: `c96cb32e059746a0c88fa39a2acb3e585126b1158189044c38653a08c36c63ab`

최종 실행 report SHA-256:

- `queries-v4/report.json`: `651b20d3c559633b4a30d91684dd2892991fe5f721b20d44f782ec3958309af4`
- `transport-v2/report.json`: `06554c4ff1efc6990f9bd4e8f7f1a019075a52dcc4439c59acdf4fbe72610228`
- `memcheck-queries-v1/report.json`: `f7afb2dc89e94f30cbb8d25c0f5e5da229a3e61f38d69fc608fd91e53db3654f`

## UX 인수인계와 다음 gate

Workspace/Batch 작업과 겹쳐 수정한 제품 파일 없음. `.bitsam` 저장 구조,
Case 결과 연결, Batch 취소/스케줄, Ray Path 형식 변경 없음.
서버·사용자 파일·다른 checkout·branch·commit·push·설치도 변경하지 않았다.
Frontend는 수정하지 않아 이번에 새 build/UI 검사는 하지 않았다.

다음 단계는 전체 oracle를 안전하게 줄일 수 있는 보수적 후보 검증 계약이다.
이후 실제 resident 광학 loop에 실험 연결하고 geometry invalidation,
overflow 주입, thread/context lifecycle, 취소/실패 복구를 확대 검증한다.
공식 성능 측정은 대표 CAD 전체 시간 20% 이상 개선과 작은 장면 3% 이내 회귀
등 기존 gate를 그대로 적용한다. Gate 미충족 시 제품 기본 경로는 유지한다.
