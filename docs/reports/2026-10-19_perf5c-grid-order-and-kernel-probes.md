# PERF 5C 히트맵 반복성과 GPU 가속 후보 비교

후속 동일 광량 shortcut, 새 CAD 탐색 후보와 304검사 결과는
[`정밀 히트맵과 CAD 탐색 후보`](2026-10-19_perf5c-uniform-grid-and-locality.md)를
따른다. 아래 측정은 이 보고서 작성 시점의 이력이다.

GPU 가속 후보를 추가 비교하고, 히트맵의 셀별 합산 순서가 정밀 반복 검사에
영향을 주는 문제를 분리했다. 셀별 광량과 제곱 광량을 GPU에서 원래 광선
순서로 합산하는 진단 경로는 확장 40조건을 모두 통과했다. 동일 GPU 조건의
히트맵도 12회 같은 값으로 반복됐다.

**CPU와 GPU 각각 2배 목표는 아직 전체 완료가 아니다.** 최신 CPU 대표 7조건은
2.154~4.824배다. 기본 GPU는 TV CAD 1.347배, 첫 면 흡광 상한 20은 1.798배로
목표에 못 미친다. 정확도 진단 경로는 일부 조건에서 느려져 기본 적용하지 않았다.

## 기본 경로에 반영한 변경

GPU 수송이 수광/반사 방출 여부를 표시할 때, 같은 실행 그룹의 활성 lane 중
대표 lane만 정수 atomic flag를 갱신하도록 바꿨다. 특정 lane이 항상 참여한다고
가정하지 않으므로 lane 0이 없거나 마지막 묶음이 덜 차 있어도 flag를 남긴다.
광량 계산, 교차 판정, RNG와 광선 순서는 바꾸지 않았다.

`LEAKAGE_GPU_PRESENCE_FLAGS=scalar`는 기존 lane별 갱신을 비교하는 내부 진단용이다.
기본값은 `warp`다. 같은 소스의 scalar/warp 교대 warm 11회는 7조건 모두 시간
회귀 3% 안이지만 속도비는 0.981~1.031배다. 큰 가속 성과나 독립적인 20%
시간 단축으로 해석하지 않는다.

## 히트맵의 합산 순서 문제

첫 관련 22검사에서는 방출광 순서 집계가 켜져 있어도 한 반복에서
`peak_effective_sample_count`가 33 ULP 차이로 32 ULP 기준을 넘었다. 상대
차이는 약 `7.0e-15`로 미세하지만 검사 실패를 보존했다. 셀별 광량/제곱 광량이
여전히 atomic 순서에 의존하기 때문이며, 광선 수를 누락한 현상은 아니다.

새 진단 경로는 다음 순서로 처리한다.

1. 기존 셀별 누적값을 GPU 내부에서 보관하고, 셀/광선 tile의 정수 hit 수를 센다.
2. hit가 있는 tile만 원래 primary 순서로 읽는다. 한 warp에 유효 표본이 하나면
   그 값만 더하고, 여러 개면 기존 exact FP64 fold를 사용한다.
3. 각 셀의 Flux와 제곱 광량을 독립적으로 갱신한다. 곱셈도 먼저 FP64로 반올림한다.

CPU로 event를 내려받아 합치지 않으며 총 광량이나 셀 크기를 바꾸지 않는다.
tile 폭을 조절해 추가 workspace를 64 MiB 이하로 제한한다. 수광이 없는 batch는
할당과 집계를 생략한다. 서로 다른 Receiver, 비영 누적값, 부분 warp, 빈 chunk,
workspace 재사용과 kernel 실패 후 중복 없는 CPU 복구를 검사했다.

`LEAKAGE_GPU_RECEIVER_GRIDS=ordered`는 진단용이고 기본값은 `atomic`이다.
모드를 바꾸면 그 모드에 맞는 GPU kernel을 다시 선택하므로, 이전 모드의 kernel이
잘못 재사용되지 않는다. 기존 방출광 순서 집계와 별개의 선택이다.

## 엄격 반복 검사

seed 7169~7173 × 8,192/65,536 Ray × 4회다. 이산 exact,
absolute/relative `1e-12`와 최대 32 ULP 기준을 유지했다.

| 방출광 | 히트맵 셀 | 이번 통과 | 최대 ULP | 현재 의미 |
| --- | --- | ---: | ---: | --- |
| atomic | atomic | 38/40 | 35 | 기본 경로의 미세한 비결정성 잔존 |
| ordered | atomic | 40/40 | 19 | 이번 반복은 통과했지만 앞선 33 ULP 실패가 있어 해결로 보지 않음 |
| ordered | ordered | 40/40 | 2 | 새 정밀 진단 경로 통과 |

기본 모드의 두 실패는 seed 7173/65,536 Ray 반복 1/2에서 방출광 34/35 ULP다.
세 모드 모두 Receiver 기여도는 CPU와 40/40 bit-exact였다. 새 셀 순서 집계의
제곱 광량은 GPU packet 순서 oracle 대비 최대 0 ULP다. CPU와 셀 Flux는 최대
1 ULP 차이가 남으므로 전체 CPU/GPU 결과가 bit-exact라고 표현하지 않는다.

같은 소스의 성능 대조군에서는 MIS 히트맵의 Flux/제곱 광량이 atomic 모드
12실행에서 서로 다른 12개 hash, ordered 모드에서는 1개 hash였다. 허용 오차를
완화하거나 실패한 첫 로그를 삭제하지 않았다.

## 히트맵 순서 집계의 비용

두 모드 모두 방출광은 atomic으로 고정한 별도 세션이다. 첫 실행을 분리하고
같은 소스에서 교대 실행한 warm 11회 중앙값이다. 모두 동일 샘플, 공개 결과의
이산 exact/물리량 `1e-9` 정합과 GPU 실행 증거를 통과했다.

| 조건 | Ray 수 | atomic 셀 | ordered 셀 | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| 반사 Receiver MIS | 65,536 | 0.04408초 | 0.04588초 | 0.961배 |
| 반사 Receiver MIS | 1,000,000 | 0.21541초 | 0.24578초 | 0.876배 |
| Face 직달 | 1,000,000 | 0.23661초 | 0.25909초 | 0.913배 |
| 확률 반사 부하 | 1,000,000 | 0.16460초 | 0.18367초 | 0.896배 |
| 갇힌 구조 상한 10 | 1,000,000 | 0.20813초 | 0.27820초 | 0.748배 |
| TV CAD 부하 | 100,000 | 0.16060초 | 0.16055초 | 1.000배 |

5조건이 시간 회귀 3% 기준을 넘는다. 정확도 검사는 통과했지만 성능 채택
기준은 실패했으며, 기본 모드나 UI에 자동 적용하지 않았다. 앞선 warm 3회에서
일부 빨라 보인 수치를 골라 성과로 사용하지 않는다.

![히트맵 순서 집계 후보의 성능 비용](../../outputs/perf5_transport/receiver-grid-ordered-final-paired.png)

## GPU 교차와 배치 후보

다음은 탐색용 비교이며 기본 경로의 변경 사항이 아니다. 기존 면 ID, FP64와
물리 입력을 유지하고, 결과가 맞아도 속도 이득이 작거나 느려지면 적용하지 않았다.

| 후보 | 관측 결과 | 판정 |
| --- | --- | --- |
| triangle을 기존 BVH leaf 순서로 배치 | CAD 약 1.001배 | 이득 부족 |
| node 배열을 묶거나 geometry까지 묶기 | CAD 최대 약 1.028배 | 이득 부족 |
| box 축 loop 펼치기 | CAD 약 1.034배, register 114→149 | 채택 기준 미달 |
| BVH/box 함수 호출 분리 | CAD 약 0.996~1.006배 | 이득 부족 |
| summary geometry 조건 compile-time 분리 | CAD 약 1.008배 | 이득 부족 |
| 가까운 child를 stack 왕복 없이 방문 | CAD 0.16414→0.22414초, 0.732배 | 느려져 폐기 |

교차 tree나 leaf 크기, tie-break를 바꾸어 면 ID 차이를 감추지 않았다.
마지막 stack 후보도 원래 방문 순서를 유지하는 비교였지만 실행 시간은 약
36.6% 증가했다. stack 왕복 감소만으로 GPU가 빨라진다고 판단하지 않는다.

배치 크기 검사에서는 별도의 병목을 확인했다. 교차 batch를 262,144/524,288로
요청해도 광원 생성기의 기본 packet은 65,536개라서 100만 Ray의 GPU 호출은
계속 16회였다. 요청 숫자만 바꾼 결과를 큰 batch 성능으로 해석하지 않는다.

그 뒤 **원래 생성된 packet을 다시 샘플링하지 않고 합치는 실험**을 실행했다.
GPU 호출은 조건에 따라 16→4/2회, CAD는 2→1회로 실제 감소했고 이산 결과와
물리량 `1e-9` 정합도 통과했다. 다만 한 정책이 모든 조건에서 빨라지지는 않았다.

| packet 병합 후보 | 주요 결과 |
| --- | --- |
| 262,144 요청과 기존 기록 window | Face 1.124배, 확률 반사 1.167배, CAD 1.033배 |
| 262,144 요청과 window 8 | 확률 반사 1.179배지만 갇힌 구조 0.857배 |
| 524,288 요청과 window 8 | GPU 호출은 더 줄어도 갇힌 구조 0.815배, MIS 0.931배 |

이는 warm 3회 탐색 수치다. 기본 packet/window를 일괄 변경하지 않았으며,
조건별 정책과 Stop·실패 복구·표시 path 보존 검증을 거친 뒤 적용해야 한다.
GPU 호출 감소율을 전체 계산 시간 감소율로 바꾸어 주장하지 않는다.

## 기본 경로의 최신 CPU와 GPU

각 장치의 고정 기준은 `157230c6`를 사용하는 기존 기준 경로다. CPU는
wavefront/4 worker와 auto 통합 수송/8 worker, GPU는 기존 GPU 함수와 현재 기본
GPU를 같은 세션에서 교대로 비교했다. 두 장치를 서로 나눈 속도비가 아니다.
아래는 warm 3회 중앙값이며 GPU의 셀/방출광은 기본 atomic 모드다.

| 조건 | CPU 기준→현재 | CPU 속도비 | GPU 기준→현재 | GPU 속도비 |
| --- | ---: | ---: | ---: | ---: |
| Face 직달 100만 Ray | 1.38067→0.40765초 | 3.387배 | 0.66037→0.27860초 | 2.370배 |
| 확률 반사 100만 Ray | 1.48263→0.51722초 | 2.867배 | 0.46712→0.22871초 | 2.042배 |
| 갇힌 구조 상한 10 100만 Ray | 8.94650→4.15381초 | 2.154배 | 0.44172→0.21973초 | 2.010배 |
| TV CAD 10만 Ray | 1.59949→0.71928초 | 2.224배 | 0.21897→0.16257초 | 1.347배 |
| 첫 면 흡광 상한 20 10만 Ray | 0.13309→0.05577초 | 2.386배 | 0.04717→0.02624초 | 1.798배 |
| 첫 면 흡광 상한 100 10만 Ray | 0.13531→0.05991초 | 2.259배 | 0.08940→0.02689초 | 3.325배 |
| 첫 면 흡광 상한 1,000 10만 Ray | 0.30283→0.06277초 | 4.824배 | 0.49187→0.02975초 | 16.531배 |

CPU 7조건은 공개 결과의 semantic hash가 bit-exact이고 fallback은 0이다.
GPU는 이산 exact와 물리량 `1e-9`를 통과했다. 갇힌 구조 GPU 2.010배는 이번
측정에서 목표를 살짝 넘은 값이며, 이전 1.907배와 함께 보면 안정적인 2배 달성으로
판정하기 이르다. Face 첫 실행은 CPU 후보 14.271초/GPU 후보 7.004초로 JIT
비용이 남는다.

CAD import, 최초 BVH build와 화면 표시는 제외했다. 확률 반사/TV는 수광 0인
부하 대조군이고, 실제 수광 정합은 앞의 MIS 검사에서 확인했다. 상한 1,000
흡광의 16.531배는 실제 1,000회 반사 가속비가 아니다.

![현재 기본 GPU 가속률](../../outputs/perf5_transport/presence-grid-production-gpu-million.png)

![현재 CPU 가속률](../../outputs/perf5_cpu/presence-grid-current-million.png)

## 통합 검사와 실행 증거

최종 관련 통합 **299검사/218.147초 통과**, 추가 관련 묶음은
**28검사/63.458초 통과**다. memcheck는 부분 warp flag, 다중 Receiver 누적과
오류 복구 **4검사/35.123초/0 errors**, synccheck는 **2검사/5.582초/0 errors**다.
`py_compile`과 `git diff --check`도 통과했다.

새 엄격 검사는 방출광과 셀을 모두 ordered로 선택한다. 기본 경로의 모든
정밀 오차가 해결됐다는 통합 테스트로 확대 해석하지 않는다. 첫 33 ULP 실패는
`presence-flags-targeted.log`에 보존했고, 해당 엄격 통합 검사는 셀까지 순서를
고정하는 경로로 보완했다. tolerance 32 ULP는 그대로다.

Delivery는 source `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`와 로컬 변경이다.
기존 source launcher 검증을 마친 `.venv-gpu`와 junction을 유지했다. 환경은
i7-10700/RTX 3070 8 GB, driver 591.59, CUDA Toolkit 13.1.1,
Python 3.13.3 x64, Numba 0.66.0/llvmlite 0.48.0이다.

실제 production preflight의 6항목을 확인했다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

선택은 `compute_backend=gpu_cuda`와 호환 BVH이며 동일 Monte Carlo 계약이다.
정상 benchmark는 모두 `gpu_active`, CPU hybrid/resident fallback 0, fallback
reason 없음이다. 기본 GPU 표의 앞 3조건은 CUDA 성공/시도 16/16, 뒤 4조건은
2/2다. 엄격 검사는 실행별 1/1이다. Face/Datum Plane emitter를 사용했고,
오류 주입에 따른 CPU 복구는 정상 GPU 성능에 섞지 않았다.

## 남은 우선순위

1. 셀별 광량이 같은 구간을 hit 수로 묶어 순서 집계의 반복 읽기 비용을 줄인다.
2. canonical source packet을 유지하면서, 깊이/기록 예산에 맞게 GPU 전달 묶음만
   조절한다. 큰 batch가 느려지는 다회 반사 조건은 별도 정책으로 검증한다.
3. CAD 교차 비용을 줄이되 기존 면 ID, 미소 간격과 tie-break를 유지한다.

CPU/GPU 전체 2배와 기본 모드의 엄격 검사 전체 통과는 남아 있다. LT/실측 정합과
1억 Ray의 절대 시간도 별도 검증이다. UI 변경, 환경 설치, 서버 재시작,
커밋과 push는 하지 않았다.

## 원본 자료

- `outputs/perf5_transport/scene-layout-probe.json`: geometry/node 배치 탐색.
- `outputs/perf5_transport/bvh-kernel-probe.json`: loop/함수/summary 분리 탐색.
- `outputs/perf5_transport/warp-flag-kernel-probe.json`: 첫 flag 후보 탐색.
- `outputs/perf5_transport/immediate-stack-probe.json`: 느려져 제외한 직접 방문 후보.
- `outputs/perf5_transport/presence-flags-paired-expanded.json`: flag 교대 warm 11회.
- `outputs/perf5_transport/presence-flags-targeted.log`: 첫 33 ULP 실패 보존.
- `outputs/perf5_transport/presence-grid-full-regression.log`: 최종 통합 299검사.
- `outputs/perf5_transport/presence-grid-targeted-final.log`: 관련 28검사.
- `outputs/perf5_transport/presence-grid-production-strict.json`: 기본 엄격 38/40.
- `outputs/perf5_transport/presence-grid-emission-only-strict.json`: 방출광만 ordered, 이번 40/40.
- `outputs/perf5_transport/receiver-grid-ordered-final-strict.json`: 둘 다 ordered, 40/40.
- `outputs/perf5_transport/receiver-grid-ordered-final-paired.json`: 최종 셀 집계 warm 11회.
- `outputs/perf5_transport/gpu-batch-window-probe.json`: 요청 크기만 바뀐 탐색.
- `outputs/perf5_transport/gpu-coalesced-batch-window-probe.json`: 실제 packet 병합 탐색.
- `outputs/perf5_transport/presence-grid-production-gpu-million.json`: 기본 GPU 고정 기준 비교.
- `outputs/perf5_cpu/presence-grid-current-million.json`: 최신 CPU 비교.
- `outputs/perf5_transport/receiver-grid-final-memcheck.log`: 메모리 검사.
- `outputs/perf5_transport/receiver-grid-final-synccheck.log`: 동기화 검사.
