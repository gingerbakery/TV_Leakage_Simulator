# PERF 5C 수광 없는 구간의 GPU 집계 생략

후속 구현과 최신 수치는
[`Receiver 순서 집계 가속과 방출광 검산`](2026-10-19_perf5c-exact-warp-fold.md)에 기록한다.
아래 수치는 이 수광 생략 단계의 측정 이력이다.

2026-10-19 검증에서 수광 없는 batch의 불필요한 Receiver 집계를 생략했다.
광선을 줄이거나 반사 상한을 바꾸지 않고, GPU 수송이 만든 수광 여부를 기존
작은 전송 packet에 함께 실어 사용한다. 같은 소스의 기능 off/auto 교대 검사에서
확률 반사 부하 대조군은 1.108배 빨라졌고, 7조건 모두 시간 회귀 3% 기준 안이었다.

**CPU/GPU 각각 2배 목표는 아직 전체 완료가 아니다.** CPU 재측정 7조건은
2.114~4.915배지만 GPU의 갇힌 구조 상한 10은 1.924배, TV CAD 상한 10은
1.351배다. 전체 엄격 수치 검사는 38/40으로 반사 방출광 합산 순서 문제가 남는다.

## 구현 내용

기존 ordered Receiver 단계는 이번 batch에 수광이 없어도 metadata workspace를
만들고 classify/순서 합산/alias 복사 kernel을 호출했다. GPU kernel 내부에서 바로
돌아와도 Python의 인자 준비와 CUDA 호출 비용은 발생했다.

이제 GPU 수송 kernel이 수광 여부를 원자적 flag로 남긴다. 이미 읽던 bounce
metadata에 포함하므로 새로운 동기화나 전송 호출을 추가하지 않는다. Receiver가
있는 summary에서 수광이 없다는 flag가 확인되면 해당 Receiver 전용 단계만 생략한다.
다른 surface 집계, 종료 집계와 실제 광선 추적은 그대로 실행한다.

- flag가 없으면 기존의 보수적인 Receiver 집계를 실행한다.
- 생략한 batch 뒤에도 기존 Receiver Flux/제곱 광량 device 상태를 보존한다.
- 나중에 실제 수광이 생기면 그때 필요한 workspace를 할당한다. 이전 workspace를
  계속 보유하는 경우 메모리 사용량에도 포함한다.
- summary가 센 수광 수와 flag가 다르거나 flag 형식이 잘못되면 성공 처리하지 않는다.
  해당 batch를 결과 반영 전에 기존 CPU 복구 경로로 재실행한다.
- summary에서 잘못된 event가 발견되면 ordered Receiver의 세 kernel도 error flag를
  확인하고 event 배열을 읽지 않는다. 원래 오류 사유와 복구 동작은 유지한다.
- 추가 전송량은 일반 batch당 16 byte, bounded window batch당 8 byte다.
  기존 packet에 실리는 양이며 telemetry와 event copy 회계에 반영한다.

내부 진단용 `LEAKAGE_GPU_RECEIVER_HIT_PROBE=off`는 기존 무조건 집계를 유지한다.
최종 구현에는 별도의 host probe 복사가 없다. Ray 수에 비례하는 event나 packet을
CPU로 내려받아 판정하지 않으며 FP64, seed, primary 순서, 광원 분포를 유지한다.

## 후보 비교와 채택 범위

첫 후보는 summary 뒤에 40 byte를 별도로 읽어 수광 여부를 확인했다. 수광이 있는
Face/갇힌 구조에서 약 6% 느려져 폐기했다. 그 뒤 기존 전송에 flag를 함께 싣는
방식으로 바꿨다.

이 방식의 첫 warm 3회 측정도 Face에서 5.4% 시간 증가를 보여 회귀 판정을
통과하지 못했다. 같은 소스를 변경하지 않고 7조건 전체를 warm 11회로 확장해
측정 분산을 확인했다. Face 시간 증가는 0.51%였고 모든 조건이 3% 안이었다.
앞선 실패 자료도 보존하며, 작은 시간 차이를 모두 알고리즘 이득으로 해석하지 않는다.

아래 표는 같은 최신 소스에서 off/auto를 같은 프로세스 안에서 교대로 실행한
warm 11회 중앙값이다. 각 모드 첫 실행도 원본 JSON에 따로 기록했다.

| 조건 | Ray 수 | Off | Auto | 속도비 | 생략한 batch |
| --- | ---: | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 0.2507초 | 0.2520초 | 0.995배 | 0/16 |
| 확률 반사 부하 대조군 | 1,000,000 | 0.2241초 | 0.2022초 | 1.108배 | 16/16 |
| 갇힌 구조 상한 10 | 1,000,000 | 0.2171초 | 0.2137초 | 1.016배 | 0/16 |
| TV CAD 상한 10 | 100,000 | 0.1625초 | 0.1620초 | 1.004배 | 2/2 |
| 첫 면 흡광 상한 20 | 100,000 | 0.0279초 | 0.0272초 | 1.024배 | 2/2 |
| 첫 면 흡광 상한 100 | 100,000 | 0.0276초 | 0.0268초 | 1.031배 | 2/2 |
| 첫 면 흡광 상한 1,000 | 100,000 | 0.0326초 | 0.0312초 | 1.047배 | 2/2 |

이는 PERF 5C의 작은 추가 개선이며 단독 20% 시간 단축이나 전체 2배 달성으로
표현하지 않는다. 수광이 있는 경로의 계산량은 줄이지 않았다. TV CAD는 별도의
교차 계산 병목이 커서 이 변경만으로 실질적인 TV 속도 개선을 주장할 수 없다.

## 정확도와 오류 복구 검사

관련 통합 **277개가 184.536초에 통과**했다. 추가한 8개는 수광 없는 여러 chunk,
실제 MIS 수광, 뒤 광원의 빈 수광, 잘못된 event, 잘못된/누락된 flag, off 진단과
Receiver 없는 장면을 다룬다. 앞 광원의 device Flux/제곱 광량과 읽기 전용 snapshot
보존, 중복 반영 없는 복구도 확인했다.

NVIDIA Compute Sanitizer를 `memcheck --target-processes all --error-exitcode 19`로
실행해 잘못된 terminal depth, 뒤 광원의 빈 수광, 실제 1,000회 반사 재시도
**3검사 통과와 0 errors**를 확인했다. `py_compile`, `git diff --check`도 통과했다.

확장 엄격 검사는 seed 7169~7173 × 8,192/65,536 Ray × 4회다. Receiver별
total/direct/reflected/lobe/depth 기여도는 CPU와 **40/40 bit-exact**이고, GPU packet의
원래 순서 oracle 대비 total Flux/제곱 광량도 최대 **0 ULP**였다. 셀별 heatmap
합산은 여전히 atomic이므로 heatmap 전체의 bit-exact를 뜻하지 않는다.

전체 strict 비교는 **38/40**이다. 두 실패 모두 seed 7172의 반사 방출광 atomic
합산으로 최대 36 ULP였다. 8,192 Ray 두 번째 반복은 lobe emitted Flux,
65,536 Ray 네 번째 반복은 depth 1 emitted Flux다. 이산 결과는 모두 exact였지만
`1e-12` absolute/relative 및 32 ULP 기준은 완화하지 않았고 검사도 실패 exit 1을
유지했다. 이전 39/40과 이번 38/40 차이를 정확도 개선이나 악화의 증거로 삼지 않는다.
기존 비결정적 합산 순서를 별도로 고쳐야 한다.

## 고정 기준 대비 GPU 성능

GPU 기준은 `157230c6`의 GPU 함수를 고정한 경로다. 같은 세션의 기준/후보 교대,
첫 실행과 warm 3회 분리, warm 중앙값이다. 위 기능 off 대비 숫자와 분모가 다르며
두 속도비를 곱하지 않는다. CPU 대비 GPU 배수도 아니다.

| GPU 조건 | Ray 수 | 기준 GPU | 현재 GPU | 속도비 | CUDA 성공/시도 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 0.6595초 | 0.2548초 | 2.588배 | 16/16 |
| 확률 반사 부하 대조군 | 1,000,000 | 0.4691초 | 0.2326초 | 2.017배 | 16/16 |
| 갇힌 구조 상한 10 | 1,000,000 | 0.4561초 | 0.2371초 | 1.924배 | 16/16 |
| TV CAD 상한 10 | 100,000 | 0.2246초 | 0.1662초 | 1.351배 | 2/2 |
| 첫 면 흡광 상한 20 | 100,000 | 0.0462초 | 0.0269초 | 1.720배 | 2/2 |
| 첫 면 흡광 상한 100 | 100,000 | 0.0900초 | 0.0274초 | 3.286배 | 2/2 |
| 첫 면 흡광 상한 1,000 | 100,000 | 0.4838초 | 0.0298초 | 16.207배 | 2/2 |

정상 비교는 모두 GPU active, CUDA 성공 1 이상, CPU hybrid/resident fallback 0,
동일 Monte Carlo 계약이다. 모든 비교의 이산 exact와 `1e-9` 물리량 허용 오차를
확인했고 source hash도 변하지 않았다. Face 후보 첫 실행은 6.799초로 cold JIT
비용이 남는다. CAD import·최초 BVH build·UI 시간은 포함하지 않는다.

확률 반사/TV 대조군은 수광 0인 부하 검사다. 실제 광량 정합은 위 살아 있는
Receiver 검사를 따른다. 상한 1,000 흡광의 16.207배는 실제 1,000회 반사가 아니라
첫 면에서 끝나는 경로의 기록 분할 비용을 줄인 결과다.

![GPU 고정 기준 대비 속도비](../../outputs/perf5_transport/receiver-hit-hint-gpu-million.png)

## 현재 CPU 재측정

현재 소스에서 CPU도 다시 측정했다. 기준은 고정 counter planner를 사용하는
wavefront/4 worker, 후보는 auto 통합 수송/8 worker다. 같은 i7-10700, 입력과 Ray 수,
같은 세션의 교대 실행 및 warm 3회 중앙값이다. 7조건 모두 공개 결과의 semantic
hash exact와 fallback 0을 확인했다.

| CPU 조건 | 기준 CPU | 현재 CPU | 속도비 |
| --- | ---: | ---: | ---: |
| Face 직달 100만 Ray | 1.3899초 | 0.4109초 | 3.382배 |
| 확률 반사 100만 Ray | 1.4772초 | 0.5230초 | 2.824배 |
| 갇힌 구조 상한 10, 100만 Ray | 8.8626초 | 4.1914초 | 2.114배 |
| TV CAD 10만 Ray | 1.5922초 | 0.7177초 | 2.219배 |
| 첫 면 흡광 상한 20, 10만 Ray | 0.1300초 | 0.0592초 | 2.194배 |
| 첫 면 흡광 상한 100, 10만 Ray | 0.1316초 | 0.0583초 | 2.256배 |
| 첫 면 흡광 상한 1,000, 10만 Ray | 0.2964초 | 0.0603초 | 4.915배 |

![CPU 고정 기준 대비 속도비](../../outputs/perf5_cpu/receiver-hit-hint-current-million.png)

## 실행 증거와 남은 범위

Delivery는 source `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`와 로컬 변경이다.
준비된 source runtime과 공유 junction `.venv-gpu`를 유지했다. 환경은
i7-10700/RTX 3070 8 GB, driver 591.59, Python 3.13.3 x64, Numba 0.66.0,
llvmlite 0.48.0, CUDA Toolkit 13.1.1이다. Face/Datum Plane emitter를 사용했다.

Production preflight는 다음 6항목을 충족했다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

정상 benchmark의 `compute_execution_state=gpu_active`, CUDA 성공/시도는 위 표와
같고 CPU hybrid/fallback 0, fallback reason 없음이다. 오류 주입 검사는 의도적인
CPU 복구이며 정상 GPU 성공으로 섞지 않았다. OS 설치/설정 변경, 서버 재시작,
커밋/push는 하지 않았다.

다음 우선순위는 수광 외 반사 방출광의 GPU 합산 순서 고정과 실제 CAD의 FP64 BVH
교차 비용 개선이다. GPU 2배, 엄격 반복 검사 전체 통과, LT/실측 정합은 아직 별도
완료 조건으로 남는다. 1억 Ray의 절대 시간도 이 작은 고정 장면에서 보장하지 않는다.

원본 수치, 실행별 timing, parity, source hash:

- `outputs/perf5_transport/receiver-hit-probe-paired.json`: 폐기한 별도 40 byte probe
- `outputs/perf5_transport/receiver-hit-hint-paired.json`: 첫 warm 3회, 회귀 판정 실패
- `outputs/perf5_transport/receiver-hit-hint-paired-expanded.json`: 같은 소스 warm 11회
- `outputs/perf5_transport/receiver-hit-hint-gpu-million.json`: 고정 GPU 기준 비교
- `outputs/perf5_cpu/receiver-hit-hint-current-million.json`: CPU 재측정
- `outputs/perf5_transport/receiver-hit-hint-expanded-strict.json`: 엄격 38/40, 실패 보존
- `outputs/perf5_transport/receiver-hit-hint-full-regression.log`: 통합 277개 통과
- `outputs/perf5_transport/receiver-hit-hint-memcheck.log`: 메모리 오류 0
