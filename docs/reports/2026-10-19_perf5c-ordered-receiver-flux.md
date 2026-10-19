# PERF-5C Receiver 광량 합산 안정화와 성능 재검증

후속 기록 window와 최신 성능은
[`GPU 반사 기록 공간과 재실행`](2026-10-19_perf5c-bounded-gpu-depth-window.md)을 따른다.
아래는 그 이전 단계의 기록이다.

2026-10-19 source checkout 결과다. **Receiver 총광량과 직달·반사·lobe·깊이별
기여도는 확장 40조건에서 CPU와 bit-exact였다.** 전체 결과의 엄격 비교는
38/40으로, 반사 방출광 집계 두 항목이 여전히 32 ULP를 넘었다.

최신 CPU 7조건은 기존 CPU 대비 warm **2.106~4.937배**다. GPU는 직달
2.290배, 갇힌 구조 2.006배지만 확률 반사 대조군 1.911배, CAD 1.353배로
목표 미달이다. **CPU·GPU 전체 2배 목표와 PERF-5C는 아직 완료되지 않았다.**

## Receiver 집계 변경

기존 Receiver total/depth Flux의 atomic 덧셈을 GPU 안의 primary 순서 합산으로
바꿨다. Receiver별 직달·반사·총합·세 반사 유형·깊이별 광량과 전체 Receiver의
직달·반사·반사 유형·깊이별 광량을 함께 처리한다. 같은 field를 반영하는
reflection/contribution 배열도 각각의 초기값부터 같은 순서로 갱신한다.

Receiver 제곱 광량 총합도 이 단계에서 함께 계산한다. 이전 별도 second-moment
분류/집계 두 kernel을 더 이상 production에서 호출하지 않으며, 기존 terminal
배열을 직접 읽는다. 전체 packet을 CPU로 내려받거나 primary별 중간 복사
배열을 추가하지 않는다. 계약은 `device_primary_ordered_receiver_flux_moments_v1`이다.

중복 계산은 **같은 packet 집합과 같은 초기 FP64 비트**가 확인될 때만 공유한다.
예를 들어 한 Receiver에 모두 같은 반사 깊이로 도달하면 총광량과 그 깊이의
광량이 같은 순서의 합이다. 서로 다른 항목의 값을 더해서 총합을 만들지 않는다.
원래 순서의 덧셈을 한 번 계산한 뒤 동일한 항목으로 복사하며, 서로 다른 초기값은
별도로 계산한다. 부호 있는 0도 초기 비트 비교에 포함한다.

일정한 광량의 tile은 이전 단계에서 검증한 IEEE 반복 덧셈 단축을 사용한다.
변하는 광량은 warp가 읽고 원래 primary 순서로 더한다. 광선 수송·반사 수식,
샘플·난수·반사 상한·FP64 정밀도는 바꾸지 않았다.

## 검증 결과

통합 **261개 테스트가 149.104초에 통과**했다. 독립적인 CPU 반복 덧셈과
1/2 Receiver, 서로 다른 비영 초기값과 0 초기값, 여러 깊이와 반사 유형,
빈 chunk와 마지막 19개 tail, workspace 재사용을 비교했다. 오류 주입 시 batch
전체 복구와 중복 반영 방지, 세 chunk의 device 상태/독립 read-only snapshot,
기존 32 KiB summary 복사량 제한도 검사했다.

확장 검사는 seed `7169~7173`, 각 `8,192/65,536 Ray`, 각 4회다.

| 검사 범위 | 결과 |
| --- | --- |
| Receiver별 total/direct/reflected/lobe/depth 기여도 | CPU와 40/40 bit-exact |
| GPU terminal packet을 원래 순서로 더한 total Flux oracle | 40/40, 최대 0 ULP |
| GPU terminal packet을 원래 순서로 더한 total 제곱 광량 oracle | 40/40, 최대 0 ULP |
| 전체 결과 strict 비교 | 38/40, 실패 시 진단 명령 exit 1 |

기존 재현 seed 7171의 8,192 Ray 별도 반복은 후보 24/24, 기준 GPU 24/24로
통과했고 후보 최대 차이는 20 ULP였다. atomic 순서가 실행마다 달라 기준 GPU가
이번 세션에 통과한 것을 과거 재현 실패가 없었다는 근거로 사용하지 않는다.

남은 전체 strict 실패는 seed 7169의 65,536 Ray 세 번째 반복에서 reflection
depth 1 emitted Flux **37 ULP**, seed 7171의 65,536 Ray 네 번째 반복에서
Lambertian emitted Flux **34 ULP**다. Receiver 총광량이 아니라 아직 atomic으로
더하는 반사 방출광 항목이다. `1e-12` absolute/relative 및 32 ULP 기준은 그대로
유지했고, Receiver 범위의 단위 검사와 전체 결과의 엄격 검사를 구분한다.

셀별 Flux/제곱 광량, 표면 profile 및 다른 반사 집계는 일부 atomic 경로를 유지한다.
Receiver 집계의 bit-exact를 Heatmap·Peak·모든 float 항목이나 LT 정합성까지
확장해서 해석하지 않는다. 현재 성능 비교의 전체 이산 exact와 `1e-9` 물리량
허용 오차는 통과했다.

## 동일 세션 성능

i7-10700과 RTX 3070 8GB에서 각 기준/후보를 같은 프로세스 안에서 교대로
실행했다. 첫 실행과 warm 3회를 따로 기록했으며 CPU/GPU benchmark는 동시에
실행하지 않았다. 아래는 warm 중앙값이다. CAD import·최초 BVH build·UI는 제외한다.

CPU 기준은 5B 입력 검증 개선 완료본의 wavefront/4 worker, 후보는 auto 통합
수송/8 worker다. 이번 단계에서 CPU 수송 알고리즘은 추가 변경하지 않았다.

| 장면 | Ray 수 | 기준 CPU | 현재 CPU | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 1.376초 | 0.412초 | 3.339배 |
| 확률 반사 대조군 | 1,000,000 | 1.473초 | 0.515초 | 2.863배 |
| 갇힌 구조 상한 10 | 1,000,000 | 8.817초 | 4.187초 | 2.106배 |
| TV CAD 부하 대조군 | 100,000 | 1.580초 | 0.721초 | 2.192배 |
| 첫 면 흡광 상한 20 | 100,000 | 0.130초 | 0.053초 | 2.460배 |
| 첫 면 흡광 상한 100 | 100,000 | 0.132초 | 0.057초 | 2.339배 |
| 첫 면 흡광 상한 1,000 | 100,000 | 0.295초 | 0.060초 | 4.937배 |

CPU 7조건의 semantic hash는 기준과 정확히 같고 native fallback은 0이다.
Face 후보의 첫 실행은 14.077초, compact kernel을 처음 사용하는 상한 20의
첫 실행은 12.831초였다. JIT를 포함한 최초 실행까지 2배라는 뜻이 아니다.

GPU 기준은 `157230c6`의 함수를 고정한 GPU 경로다. CPU 대비 GPU 배수가 아니다.

| 장면 | 기준 GPU | 현재 GPU | 속도비 | CUDA 성공/시도 |
| --- | ---: | ---: | ---: | ---: |
| Face 직달 100만 Ray | 0.655초 | 0.286초 | 2.290배 | 16/16 |
| 확률 반사 대조군 100만 Ray | 0.446초 | 0.233초 | 1.911배 | 16/16 |
| 갇힌 구조 상한 10, 100만 Ray | 0.476초 | 0.237초 | 2.006배 | 16/16 |
| TV CAD 부하 대조군 10만 Ray | 0.224초 | 0.166초 | 1.353배 | 2/2 |
| 첫 면 흡광 상한 20, 10만 Ray | 0.050초 | 0.032초 | 1.566배 | 2/2 |
| 첫 면 흡광 상한 100, 10만 Ray | 0.132초 | 0.057초 | 2.320배 | 7/7 |
| 첫 면 흡광 상한 1,000, 10만 Ray | 0.476초 | 0.259초 | 1.839배 | 49/49 |

정상 측정은 모두 `gpu_active`, CUDA 성공 1 이상, resident fallback/CPU hybrid 0,
`cpu_gpu_deterministic_batch_v1`이었다. Face/Datum Plane emitter를 사용했고
Face 후보 첫 실행은 6.727초였다. 확률 반사와 TV CAD 대조군은 수광 0인 계산
부하 검사이므로 광량 정확도는 위의 별도 Receiver 검사를 따른다. 흡광 상한
1,000은 실제 1,000회 반사 성능이 아니라 첫 면에서 모두 흡수되는 조건이다.

추가 metadata는 기본 batch에서 약 128~281 KiB, 상한 1,000의 작은 batch에서
약 535 KiB였다. primary 수와 논리 반사 상한의 곱만큼 별도 packet을 예약하지
않고, field/tile 수에 따라 제한한다. `receiver_moment_workspace_bytes=0`이며
통합 metadata를 `receiver_flux_workspace_bytes`에 한 번만 기록한다.

같은 최신 코드의 atomic 진단 모드와도 교대로 비교했다. 순서 고정은 Face
11.55%, 확률 반사 9.39%, 갇힌 구조 10.49%, CAD 0.66%의 추가 시간이 들었다.
정확도 안정화 자체를 가속으로 해석하지 않는다. GPU 단계 계측은 추가 동기화를
사용하므로 그 시간과 end-to-end 차이를 단순 합산하지 않는다. 이 비용도 다음
최적화 대상이다.

## GPU 실행 블록 크기 대조

같은 kernel의 실행 블록만 128에서 32/64/256 threads로 바꿔 별도 측정했다.
Face는 최대 1.128배였지만 CAD는 최대 1.038배에 그쳤고, 256은 확률 반사에서
0.899배로 느려졌다. 모든 이산/물리량 비교는 통과했으나 일관된 20% 단축이
없어 production의 128 threads를 유지했다.

## 다음 병목과 실행 기록

남은 반사 방출광의 합산 순서를 안정화하고, GPU의 논리 반사 상한과 실제 기록
공간을 분리해야 한다. 현재 GPU는 상한 1,000에서 첫 면에 끝나는 경로도
2,048개씩 나눠 처리한다. bounded depth window와 안전한 분할/재실행, 직접
summary는 아직 후속이며 CAD의 FP64 BVH 교차 비용도 별도로 줄여야 한다.

전달 경로는 `main@157230c6` source checkout과 로컬 변경이다. production
preflight는 `available/strict_float64/kernel_executed/kernel_verified=true`,
`preflight_scope=production_ray_bvh`, `provider_contract=strict_float64_bvh_v1`로
통과했다. 기존 launcher가 준비한 공유 junction `.venv-gpu`를 유지했고 OS 설치,
시스템 설정 변경, 서버 재시작, 커밋/push는 하지 않았다.

원본 자료는 다음과 같다. 성능과 확장 packet 진단의 source hash 및
`source_unchanged=true`를 보존한다. packet 다운로드 진단은 성능 측정에 포함하지 않는다.

- `outputs/perf5_cpu/receiver-flux-current-million.json`
- `outputs/perf5_transport/receiver-flux-joint-gpu-million.json`
- `outputs/perf5_transport/receiver-flux-joint-expanded.json`
- `outputs/perf5_transport/receiver-flux-joint-full-regression.log`
- `outputs/perf5_transport/receiver-flux-blocks.json`
- `outputs/perf5_transport/receiver-flux-current-mode-paired.json`
- `outputs/perf5_transport/receiver-flux-current-roundoff.json`

성능 그림은 `outputs/perf5_cpu/receiver-flux-current-million.png`와
`outputs/perf5_transport/receiver-flux-joint-gpu-million.png`다.
