# PERF 5C Receiver 순서 집계 가속과 방출광 검산

셀별 반복성 및 추가 가속 후보의 후속 결과는
[`히트맵 반복성과 GPU 가속 후보 비교`](2026-10-19_perf5c-grid-order-and-kernel-probes.md)를 따른다.
아래 수치는 Receiver fold 단계의 측정 이력이다.

GPU Receiver 집계의 FP64 덧셈 결과를 보존하면서, 같은 지수 구간에서 정수
연산으로 묶어 계산하는 경로를 추가했다. 같은 소스의 기존 순차 집계와 교대
측정한 반사 MIS 조건은 1.119~1.223배 빨라졌다. 직달광에는 기존의 작은
kernel을 유지해 불필요한 자원 사용을 피했다. 6개 대조군은 시간 회귀 3% 안이었다.

**CPU와 GPU 각각 2배 목표는 전체 완료가 아니다.** 최신 CPU 7조건은
2.143~5.068배다. GPU는 갇힌 구조 1.907배, TV CAD 1.352배로 목표에 못 미친다.
기본 GPU 경로의 엄격 수치 검사는 반사 방출광 atomic 합산 때문에 36/40이다.
방출광을 순서 집계하는 진단 후보는 40/40이지만, 반사 부하에서 느려져
기본값으로 적용하지 않았다.

## Receiver 집계 변경

기존 방식은 광선 순서를 보존하기 위해 32개 lane의 광량을 하나씩 가져와
한 lane에서 더했다. 새 경로는 현재 누적값과 다음 덧셈들이 같은 이진 지수
구간에 머무르면, 각 덧셈이 FP64 표현에서 이동시키는 ULP 수를 정수로 합친다.
정확히 중간에 놓이는 반올림은 앞선 누적값의 홀짝을 함께 계산한다.

지수 경계를 넘거나 조건이 성립하지 않으면 원래 순서의 FP64 덧셈을 GPU에서
그대로 실행한다. CPU로 event를 내려받거나 합산 순서를 임의로 재배치하지 않는다.
광량의 제곱도 `dmul_rn`으로 먼저 반올림해 곱셈과 덧셈의 융합으로 결과가
달라지는 것을 막았다. 기존 동일 광량 반복 집계의 덧셈에도 `dadd_rn`을 사용한다.

발광 후 반사가 없고 primary 광량의 bit가 모두 같은 batch는 기존 순차 경로의
작은 kernel을 선택한다. 수송 중 방출 여부를 기존 metadata packet에 함께 실어
추가 host 동기화 호출 없이 판단하며, 전송 회계에는 batch당 8 byte를 추가했다.
이 선택은 kernel 자원 사용을 줄이는 힌트일 뿐이다. 작은 kernel도 가변 광량을
원래 순서로 합칠 수 있으므로 힌트만 믿고 다른 광량을 같다고 처리하지 않는다.

## 같은 소스의 순차 집계와 비교

기존 순차 fold와 새 fold를 같은 프로세스에서 교대로 실행했다. 각 모드의 첫
실행과 warm 11회를 분리했고 아래는 warm 중앙값이다. 반사 방출광은 두 모드
모두 기본 atomic 방식으로 고정했다. 모든 실행의 Receiver 기여도가 bit-exact,
공개 결과는 이산 exact와 물리량 `1e-9` 기준을 통과했고 소스 hash도 유지됐다.

| 조건 | Ray 수 | 순차 fold | 새 fold | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| 반사 Receiver MIS | 65,536 | 0.04603초 | 0.04115초 | 1.119배 |
| 반사 Receiver MIS | 1,000,000 | 0.26456초 | 0.21634초 | 1.223배 |
| Face 직달 | 1,000,000 | 0.23442초 | 0.22777초 | 1.029배 |
| 확률 반사 부하 대조군 | 1,000,000 | 0.20653초 | 0.20892초 | 0.989배 |
| 갇힌 구조 상한 10 | 1,000,000 | 0.21510초 | 0.21647초 | 0.994배 |
| TV CAD 부하 대조군 | 100,000 | 0.16101초 | 0.16067초 | 1.002배 |

100만 Ray MIS의 시간 단축은 약 18.2%다. 이번 추가 개선만으로 독립적인
20% 시간 단축 기준이나 전체 2배 목표를 달성했다고 표현하지 않는다. Face는
두 모드 모두 같은 작은 kernel을 사용하므로 약 3% 차이를 새 fold의 이득으로
해석하지 않는다. 다른 장면에서 가속을 얻으려고 광선이나 반사 횟수를 줄이지 않았다.

앞선 공통 kernel 후보는 Face에서 3.84%, 함수를 별도 호출로 분리한 후보는
7.74% 느려져 채택하지 않았다. 직달광 분리 후 첫 측정도 도중에 안전 guard를
수정해 `source_unchanged=false`였으므로 통과 근거에서 제외했다. 위 표는 이후
소스를 고정하고 다시 실행한 `receiver-warp-fold-dispatch-verified.json` 결과다.

## 반사 방출광의 순서 집계 후보

Receiver 외의 반사 방출광은 아직 global FP64 atomic 덧셈을 사용한다. 합산
순서의 비결정성 때문에 엄격 ULP 검사에서 실패하는 문제가 남아 있다.
이를 분리 검증하려고 GPU 안에서 primary 순서로 방출광을 합산하는 후보를
추가했다. `LEAKAGE_GPU_EMITTED_FLUX=ordered`는 내부 진단 전용이며 기본값은
계속 `atomic`이다.

진단 후보는 lobe/depth 출력별 block에서 4,096개씩 처리한다. 동일 광량 반복,
지수 구간 안의 정수 ULP 합산, 원래 순차 덧셈을 구분한다. reflection과
contribution의 초기 누적값이 다르면 별도로 계산한다. 추가 workspace는 출력
그룹당 24 byte이며 Ray 수와 반사 상한을 곱한 새 임시 배열은 만들지 않는다.
방출이 없는 batch는 이 workspace와 단계 자체를 생략한다.

잘못된 event나 방출 여부 힌트는 성공 처리하지 않고 기존 복구 경로로 재실행한다.
NaN 방출광이 동일 값 tile로 잘못 합쳐지지 않는 검사도 추가했다. 최초 racecheck에서
발견한 shared 누적값의 읽기/쓰기 경합은 warp 동기화를 추가해 수정했다.

최신 같은 소스의 atomic/ordered 교대 warm 11회는 다음과 같다. 공개 결과의
물리량 정합과 GPU 실행 증거, 소스 고정은 모두 통과했다. 아래 수치는 위 fold
비교와 별도 세션이므로 시간을 직접 섞거나 속도비를 곱하지 않는다.

| 조건 | 기본 atomic | 진단 ordered | 시간 증가 |
| --- | ---: | ---: | ---: |
| Face 직달 100만 Ray | 0.17676초 | 0.17874초 | 1.12% |
| 확률 반사 부하 100만 Ray | 0.20138초 | 0.23698초 | 17.68% |
| 갇힌 구조 100만 Ray | 0.20908초 | 0.27984초 | 33.84% |
| TV CAD 10만 Ray | 0.16204초 | 0.17008초 | 4.96% |

첫 warm 3회에서는 Face도 8.02% 느려져 같은 소스를 warm 11회로 확장했다.
방출 자체가 없는 Face의 차이는 측정 분산과 구분해야 한다. 실제 반사 부하의
비용은 확장 측정에서도 남아 있어, 정확도 후보를 속도 개선으로 승격하지 않는다.

## 정확도와 안전성 검사

- 관련 통합은 **서로 다른 289검사 통과**다. 실행 로그는 298회/193.159초이며
  근접 Target 9검사가 명령에 중복 포함됐다. 이를 298개의 서로 다른 검사로 세지 않는다.
- 새 warp fold는 반올림 tie, 지수 경계, 마스크, subnormal, overflow, 부호와
  제곱 후 반올림을 포함한 **3,412개 수치 표본에서 bit-exact**였다.
- memcheck는 warp fold, 직달 kernel 분리, tile 경계와 NaN 검사 등
  **5검사 통과/43.459초/0 errors**다.
- 수정한 방출광 block의 racecheck는 **0 hazards/0 errors/0 warnings**, synccheck는
  **0 errors**였다. `py_compile`과 `git diff --check`도 통과했다.

엄격 반복 검사는 seed 7169~7173 × 8,192/65,536 Ray × 4회다. 두 방출광 모드
모두 Receiver의 total/direct/reflected/lobe/depth 기여도는 CPU와 **40/40
bit-exact**다. GPU packet 원래 순서 oracle 대비 Receiver 총광량/제곱 광량
총합도 최대 **0 ULP**였다. 셀별 heatmap atomic 합산까지 bit-exact라는 의미는 아니다.

| 반사 방출광 모드 | 전체 엄격 검사 | 최대 ULP | 기본 적용 |
| --- | ---: | ---: | --- |
| atomic | 36/40 | 38 | 유지 |
| ordered | 40/40 | 17 | 진단 전용 |

기본 모드 실패는 65,536 Ray의 seed 7169 반복 0, 7171 반복 1, 7172 반복
0/2에서 방출광 35/38/38/37 ULP다. 이산 결과는 모두 exact이며 차이는 약
`1e-15` 상대 오차 수준이지만, `1e-12` absolute/relative와 32 ULP 기준을
완화하거나 실패 exit를 성공으로 바꾸지 않았다. 이전 37/40이나 38/40과의
통과 개수 차이는 비결정적 atomic 결과이며 개선/악화의 근거로 삼지 않는다.

## 고정 GPU 기준 대비 최신 성능

기준은 `157230c6`의 GPU 함수를 고정한 경로다. 같은 세션에서 기준/후보를
교대로 실행한 warm 3회 중앙값이며 후보는 기본 atomic 방출광 모드다.
CAD import, 최초 BVH build와 화면 표시는 계산 시간에 포함하지 않는다.

| GPU 조건 | Ray 수 | 기준 GPU | 현재 GPU | 속도비 | CUDA 성공/시도 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 0.65417초 | 0.28501초 | 2.295배 | 16/16 |
| 확률 반사 부하 대조군 | 1,000,000 | 0.36171초 | 0.14461초 | 2.501배 | 16/16 |
| 갇힌 구조 상한 10 | 1,000,000 | 0.45460초 | 0.23838초 | 1.907배 | 16/16 |
| TV CAD 상한 10 | 100,000 | 0.22486초 | 0.16628초 | 1.352배 | 2/2 |
| 첫 면 흡광 상한 20 | 100,000 | 0.04712초 | 0.02764초 | 1.705배 | 2/2 |
| 첫 면 흡광 상한 100 | 100,000 | 0.08998초 | 0.02805초 | 3.208배 | 2/2 |
| 첫 면 흡광 상한 1,000 | 100,000 | 0.48939초 | 0.03123초 | 15.671배 | 2/2 |

공개 결과의 이산 exact/물리량 `1e-9` 정합과 소스 고정을 확인했다. 모든 정상
GPU 비교는 `gpu_active`, CPU hybrid/resident fallback 0, fallback reason 없음이다.
Face 후보 첫 실행은 6.515초로 cold JIT 비용이 남는다. 확률 반사와 TV CAD는
수광 0인 부하 대조군이며 실제 광량 정합은 앞의 MIS 검사에서 확인했다.
상한 1,000 흡광의 15.671배는 실제 1,000회 반사를 계산한 가속비가 아니다.

![고정 GPU 기준 대비 최신 가속률](../../outputs/perf5_transport/warp-fold-dispatch-gpu-million.png)

## 최신 CPU 재측정

같은 최신 소스에서 CPU도 다시 측정했다. 고정 counter planner의 wavefront/
4 worker와 auto 통합 수송/8 worker를 동일 i7-10700과 같은 물리 입력으로
교대 실행했다. warm 3회 중앙값이며 7조건 모두 공개 결과의 semantic hash가
bit-exact이고 fallback은 0이다. GPU와 비교한 배수가 아니다.

| CPU 조건 | 기준 CPU | 현재 CPU | 속도비 |
| --- | ---: | ---: | ---: |
| Face 직달 100만 Ray | 1.39046초 | 0.41032초 | 3.389배 |
| 확률 반사 100만 Ray | 1.48755초 | 0.51290초 | 2.900배 |
| 갇힌 구조 상한 10 100만 Ray | 8.97248초 | 4.18675초 | 2.143배 |
| TV CAD 10만 Ray | 1.59846초 | 0.73324초 | 2.180배 |
| 첫 면 흡광 상한 20 10만 Ray | 0.13112초 | 0.05352초 | 2.450배 |
| 첫 면 흡광 상한 100 10만 Ray | 0.13505초 | 0.05679초 | 2.378배 |
| 첫 면 흡광 상한 1,000 10만 Ray | 0.30311초 | 0.05980초 | 5.068배 |

처음 Face 실행은 기준 5.719초/후보 14.404초, 처음 bounded 흡광 경로는 후보
12.803초다. warm 2배 통과를 첫 JIT 실행이나 모든 사용자 모델의 2배 보장으로
확장하지 않는다.

![고정 CPU 기준 대비 최신 가속률](../../outputs/perf5_cpu/warp-fold-dispatch-current-million.png)

## 실행 환경과 남은 개발

Delivery는 source `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`와 로컬 변경이다.
기존 launcher 검증을 마친 `.venv-gpu` runtime/junction을 유지했다. 환경은
i7-10700/RTX 3070 8 GB, driver 591.59, Python 3.13.3 x64, Numba 0.66.0,
llvmlite 0.48.0, CUDA Toolkit 13.1.1이다. Face와 Datum Plane emitter를 사용했다.

이번 실제 장치 검사에서도 다음 production preflight 6항목을 모두 확인했다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

선택은 `compute_backend=gpu_cuda`, 호환 BVH 경로와 동일 Monte Carlo 계약이다.
엄격 반복 검사는 각 실행 `gpu_active`, CUDA 성공/시도 1/1, resident fallback
0이었다. 고정 기준 성능 검사의 성공/시도는 표와 같고 CPU hybrid도 0이었다.
오류 주입에 의한 복구를 정상 GPU 성능에 섞지 않았다.

다음 우선순위는 실제 CAD의 FP64 BVH 교차 비용, 작은 batch의 고정 호출 비용과
방출광 순서 집계의 병렬화다. 원본 면 ID와 미소 간격 tie-break를 유지해야 한다.
GPU 대표 조건 전체 2배, 기본 모드의 엄격 검사 전체 통과, LT/실측 정합은 남아
있다. 1억 Ray의 절대 시간도 이번 작은 고정 장면에서 보장하지 않는다.
OS 설치/설정 변경, 서버 재시작, 커밋과 push는 하지 않았다.

## 원본 실행 자료

- `outputs/perf5_transport/receiver-warp-fold-controls.json`: Face 회귀로 제외한 공통 kernel.
- `outputs/perf5_transport/receiver-warp-fold-outlined-controls.json`: Face 회귀로 제외한 별도 함수 호출 후보.
- `outputs/perf5_transport/receiver-warp-fold-dispatch-controls.json`: 소스 변경으로 무효인 첫 분리 측정.
- `outputs/perf5_transport/receiver-warp-fold-dispatch-verified.json`: 최종 소스 고정 warm 11회 대조군.
- `outputs/perf5_transport/warp-fold-dispatch-gpu-million.json`: 고정 GPU 기준 최신 비교.
- `outputs/perf5_cpu/warp-fold-dispatch-current-million.json`: 최신 CPU 비교.
- `outputs/perf5_transport/warp-fold-dispatch-production-strict.json`: 기본 엄격 36/40, 실패 보존.
- `outputs/perf5_transport/warp-fold-dispatch-ordered-strict.json`: 진단 후보 엄격 40/40.
- `outputs/perf5_transport/warp-fold-dispatch-emitted-cost.json`: 진단 후보 비용 warm 3회.
- `outputs/perf5_transport/warp-fold-dispatch-emitted-cost-expanded.json`: 같은 소스 warm 11회 확장.
- `outputs/perf5_transport/warp-fold-dispatch-full-regression.log`: 289종류/298실행 통합 검사.
- `outputs/perf5_transport/warp-fold-dispatch-memcheck.log`: 메모리 검사 0 errors.
- `outputs/perf5_transport/ordered-fold-fixed-racecheck.log`: 수정 후 racecheck 0 hazards.
- `outputs/perf5_transport/ordered-fold-fixed-synccheck.log`: 수정 후 synccheck 0 errors.
