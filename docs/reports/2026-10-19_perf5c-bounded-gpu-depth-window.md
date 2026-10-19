# PERF-5C GPU 반사 기록 공간과 재실행 개선

후속 수광 없는 batch 처리와 최신 재측정은
[`수광 없는 GPU 집계 생략`](2026-10-19_perf5c-zero-hit-receiver-stage.md)을 따른다.

2026-10-19 source checkout 검증 결과다. **반사 상한만 높은 짧은 경로의 GPU
분할 횟수를 줄였다.** 같은 최신 코드에서 기능을 끈 경우와 비교하면 상한
1,000의 첫 면 흡광은 6.696배, TV 코너 CAD는 3.548배 빨랐다. 후자는 실제
100,000 Ray와 1,903,092 surface hit를 그대로 계산한 결과다.

전체 성능 목표는 아직 미완료다. 현재 CPU 7조건은 기준 대비 2.154~4.701배지만,
기존 GPU 함수와 비교한 일반 TV CAD 상한 10은 1.344배다. GPU 전체 엄격 검사도
39/40으로 반사 방출광 합산 순서 문제가 남아 있다.

## 구현 범위

기존 GPU는 `primary 수 × (설정 반사 상한+1)`으로 한 batch를 제한했다. 상한
1,000이면 실제 첫 면에서 끝나는 100,000 Ray도 2,048개씩 49회 나눠 처리했다.

이제 compact summary에서 전체 논리 깊이의 기록이 기존 예산에 들어가면 그대로
사용하고, 그렇지 않으면 32-depth 기록 window로 시작한다. 상한은 여전히 1,000이고
기록 공간만 작아진다. 성공한 batch는 기존 GPU summary와 표시 경로를 반영한다.

실제 surface event가 window를 넘으면 해당 시도를 **summary 반영 전에** 중단한다.
다음 기록 깊이를 늘리고 필요하면 primary batch를 줄여 같은 시작 위치에서 다시
실행한다. 원래 global primary index, seed, 광량과 반사 상한을 유지한다. 예산 초과를
CPU fallback이나 정상 종료로 취급하지 않으며 Ray나 깊이를 잘라내지 않는다.

- 각 계산 workspace의 event slot은 기존 `2,097,152` 이하를 유지한다.
- 완료된 앞 chunk는 유지하고, 미반영 chunk만 재시도한다. 재시도 경계에서 Stop도 확인한다.
- 실제 GPU 오류가 나면 해당 chunk만 기존 경로로 복구한다. 큰 chunk의 CPU 복구도
  원래 event 예산에 맞게 나누므로 이미 완료한 앞부분을 중복 계산하지 않는다.
- 표시용 sparse path는 논리 깊이를 수용하는 별도 geometry workspace로 재추적한다.
  표시 path quota가 큰 조건은 full-depth 예산에 맞춰 batch를 보수적으로 제한한다.

계약은 `bounded_depth_window_replay_before_commit_v1`이다. 결과에는 실제 최대
batch, 기록 용량, 재시도 횟수와 버린 시도의 시간을 별도로 기록한다. 기록 용량은
물리적 반사 횟수가 아니며 실제 방문 깊이는 기존 통계를 따른다.

## 자동 적용 정책

모든 batch에 무조건 window를 적용하는 후보는 작은 깊은 경로에서 약 26~40%의
추가 시간이 들었다. 전체 기록이 이미 예산에 들어가는 경우에는 재시도할 이유가
없으므로 최종 `auto`는 기존 full-depth 기록을 바로 사용한다. 강제 window는 내부
검증용이다. 최종 작은 깊은 대조군의 최악 시간 증가는 약 0.20%로 3% 기준 안이었다.

일반 kernel과 bounded kernel을 따로 컴파일하는 후보도 비교했지만 일관된 이득이
없어 채택하지 않았다. 현재 제품 코드는 하나의 kernel을 사용한다.

## 검증 결과

관련 통합 **269개가 182.191초에 통과**했다. 추가로 NVIDIA Compute Sanitizer
memcheck에서 late deep emitter와 Stop 경계 두 검사를 실행해 **0 errors**를 확인했다.

| 검사 | 확인 내용 |
| --- | --- |
| 상한 1,000, 첫 면 흡광, 70,019 Ray | 65,536 크기의 GPU batch 사용, 완료 2회, 누락·중복·fallback 0 |
| 실제 1,000회 반사, 17 Ray | surface hit 17,017, 저장 path 8개, 깊이 축소 없이 강제 재시도 통과 |
| 축소 예산 2,048 slot | 필요한 batch 분할, 실제 교차 수와 primary 순서 유지 |
| 앞 광원은 흡광, 뒤 광원은 깊은 반사 | 실패한 시도에서 이미 반영된 device summary/digest 불변 |
| 첫 큰 chunk 성공 후 두 번째 GPU 오류 | 완료된 앞부분 보존, suffix만 CPU 복구, mixed 실행으로 정직하게 보고 |
| 재시도 도중 Stop | 미완료 시도의 Ray와 event를 결과에 반영하지 않음 |
| Face emitter와 살아 있는 Receiver | 큰 batch에서도 수광 수·Flux·Heatmap 정합 유지 |
| 사용하지 않는 실제 기록 slot 오염 | stale face/NaN을 읽지 않음 |

수광면 없는 평행 반사면 캐비티 검사에서는 빈 배열의 해시 처리 오류도 수정했다.
이제 CPU 대체 실행 없이 실제 GPU 수송으로 해당 조건을 검증한다.

확장 정밀 검사는 seed 7169~7173, 8,192/65,536 Ray, 각 4회다. Receiver별
total/direct/reflected/lobe/depth 기여도는 CPU와 40/40 bit-exact이고, GPU packet의
원래 순서 oracle 대비 total Flux/제곱 광량도 최대 0 ULP였다. 전체 strict 비교는
39/40으로 seed 7170의 65,536 Ray 세 번째 반복에서 depth 1 emitted Flux가
38 ULP였다. `1e-12` absolute/relative 및 32 ULP 기준은 완화하지 않았다.
atomic 합산의 통과 횟수가 실행마다 달라지는 현상은 이번 window 변경으로 해결한
것이 아니다. Receiver 밖의 반사 방출광 순서 고정이 계속 필요하다.

## Window 자체의 성능

RTX 3070에서 같은 최신 코드의 `off/auto`를 같은 프로세스 안에서 교대로
실행했다. 각 첫 실행과 warm 3회를 분리했다. 아래는 warm 중앙값이며 CPU/GPU
benchmark는 동시에 실행하지 않았다. CUDA 실행 증명과 이산 exact, `1e-9`
물리량 허용 오차를 모든 비교에 요구했다.

| 조건 | Ray 수 | Window off | Auto | 속도비 | 완료 GPU batch |
| --- | ---: | ---: | ---: | ---: | ---: |
| 첫 면 흡광, 상한 100 | 100,000 | 0.0942초 | 0.0497초 | 1.894배 | 7 → 2 |
| 첫 면 흡광, 상한 1,000 | 100,000 | 0.3377초 | 0.0504초 | 6.696배 | 49 → 2 |
| 실제 상한 80까지 반사 | 10,000 | 0.0969초 | 0.0835초 | 1.161배 | 1 → 1 |
| 실제 상한 1,000까지 반사 | 512 | 0.2173초 | 0.2079초 | 1.046배 | 1 → 1 |
| 뒤 광원에서 깊은 반사 발생 | 1,024 | 0.0922초 | 0.0924초 | 0.998배 | 2 → 2 |
| TV 코너 CAD, 상한 1,000 | 100,000 | 0.8931초 | 0.2517초 | 3.548배 | 49 → 2 |

마지막 CAD의 실제 교차는 1,903,092개로 양쪽이 같다. 실제 1,000회 반사하는
캐비티와, 상한만 1,000인 CAD/흡광 조건을 구분한다. 예산에 이미 들어가는 깊은
작은 조건은 별도의 kernel 최적화로 더 빨라진 것이 아니므로 작은 시간 변화를
일반적인 가속 보장으로 해석하지 않는다.

## 고정 기준 대비 현재 성능

GPU 기준은 `157230c6`의 GPU 함수를 고정한 경로다. 위 window off보다 더 이전
버전이며 CPU 대비 GPU 배수도 아니다. 같은 세션에서 기준/후보를 교대로 비교한
warm 3회 중앙값이다. CAD import·최초 BVH build·UI 시간은 제외한다.

| GPU 장면 | Ray 수 | 기준 GPU | 현재 GPU | 속도비 | CUDA 성공/시도 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 0.6426초 | 0.2667초 | 2.410배 | 16/16 |
| 확률 반사 부하 대조군 | 1,000,000 | 0.4740초 | 0.2382초 | 1.990배 | 16/16 |
| 갇힌 구조 상한 10 | 1,000,000 | 0.4566초 | 0.2432초 | 1.878배 | 16/16 |
| TV CAD 상한 10 | 100,000 | 0.2207초 | 0.1642초 | 1.344배 | 2/2 |
| 첫 면 흡광 상한 20 | 100,000 | 0.0480초 | 0.0268초 | 1.790배 | 2/2 |
| 첫 면 흡광 상한 100 | 100,000 | 0.0891초 | 0.0279초 | 3.194배 | 2/2 |
| 첫 면 흡광 상한 1,000 | 100,000 | 0.5391초 | 0.0338초 | 15.946배 | 2/2 |

정상 비교는 모두 `gpu_active`, CUDA 성공 1 이상, CPU hybrid/resident fallback 0,
`cpu_gpu_deterministic_batch_v1`이다. Face/Datum Plane emitter를 사용했다. Face
후보 첫 실행은 6.650초로 cold JIT 비용은 여전히 크다. 확률 반사와 TV CAD
대조군은 수광 0인 부하 검사이므로 광량 정확도는 별도의 살아 있는 Receiver 검사를
따른다. 1.990배를 2배 달성으로 반올림하지 않는다.

CPU도 현재 소스로 재측정했다. 기준은 5B 입력 검증 개선 완료 wavefront/4 worker,
후보는 auto 통합 수송/8 worker다. 7조건 semantic hash exact와 fallback 0이었다.

| CPU 장면 | 기준 CPU | 현재 CPU | 속도비 |
| --- | ---: | ---: | ---: |
| Face 직달 100만 Ray | 1.3955초 | 0.4107초 | 3.398배 |
| 확률 반사 100만 Ray | 1.4750초 | 0.5225초 | 2.823배 |
| 갇힌 구조 상한 10, 100만 Ray | 8.9820초 | 4.1695초 | 2.154배 |
| TV CAD 10만 Ray | 1.5857초 | 0.7168초 | 2.212배 |
| 첫 면 흡광 상한 20, 10만 Ray | 0.1318초 | 0.0524초 | 2.514배 |
| 첫 면 흡광 상한 100, 10만 Ray | 0.1314초 | 0.0586초 | 2.243배 |
| 첫 면 흡광 상한 1,000, 10만 Ray | 0.2962초 | 0.0630초 | 4.701배 |

## 실행 기록과 남은 작업

환경은 source `main@157230c6`와 로컬 변경, i7-10700/RTX 3070이다. 준비된
source runtime에서 production preflight의 `available/strict_float64/kernel_executed/
kernel_verified=true`, `preflight_scope=production_ray_bvh`,
`provider_contract=strict_float64_bvh_v1`을 확인했다. OS 설치·설정 변경, 서버
재시작, 커밋/push는 하지 않았고 공유 junction `.venv-gpu`를 유지했다.

다음은 수광 0인 chunk의 불필요한 ordered Receiver 단계, CUDA 호출/준비 비용과
CAD의 FP64 BVH 교차 비용이다. 엄격 반사 방출광 합산도 계속 수정해야 한다.
기록 window는 실제 방문 event 수만큼 저장하는 완전한 GPU event pool은 아니며,
희귀한 깊은 경로 하나가 이후 batch 크기를 낮추는 한계도 남는다. 이 범위를
GPU 전체 2배 또는 RT 정확도 전체 검증 완료로 부르지 않는다.

원본 자료와 source hash는 다음 파일에 보존한다. 최종 소스는 kernel 분리 후보를
제외한 accepted 측정 소스와 일치한다.

- `outputs/perf5_transport/event-window-auto-paired.json`
- `outputs/perf5_transport/event-window-gpu-million.json`
- `outputs/perf5_cpu/event-window-current-million.json`
- `outputs/perf5_transport/event-window-expanded-strict.json`
- `outputs/perf5_transport/event-window-full-regression.log`
- `outputs/perf5_transport/event-window-memcheck.log`

성능 그림은 `outputs/perf5_transport/event-window-gpu-million.png`와
`outputs/perf5_cpu/event-window-current-million.png`다.
