# PERF-5B CPU 높은 반사 상한 기록 개선

후속 GPU 제곱 광량 집계, CPU 상한 16 이상 compact 적용 및 최신 검증은
[`GPU 합산 순서 안정화`](2026-10-19_perf5c-ordered-receiver-moments.md)를 따른다.
아래는 그 이전 단계의 기록이다.

2026-10-19, source checkout의 로컬 구현과 측정 결과다. **CPU 7개 측정 조건은
기존 CPU 대비 warm 2.020~4.874배였지만 CPU·GPU 전체 2배 목표는 아직 미달이다.**
GPU CAD 대조군은 1.339배였고, 별도의 엄격한 MIS 반복 검사는 8회 중 1회 실패했다.
PERF-5C 전체 완료나 모든 모델의 2배 성능을 의미하지 않는다.

## 구현 범위

이전 CPU 자동 통합 수송은 상한 32 이하에서만 사용했다. 높은 상한에서는
`primary 수 × 허용 깊이`를 기준으로 batch가 작아져, 실제로 첫 면에서 모두
흡수되는 장면도 불필요하게 여러 번 나눠 처리했다.

상한 32 초과에서는 worker별로 분리된 고정 예산의 event pool에 실제 방문한
event를 연속 기록한다. 최대 2,097,152 event slot을 예약하고, 사용한 구간만
원래 primary 순서의 독립 소유/read-only 배열로 옮긴다. 낮은 상한의 기존
dense 경로는 유지한다. 상한 1,000에서 첫 batch는 여전히 보수적인 2,048개로
시작하지만 관측한 실제 event 수에 따라 최대 32,768개까지 늘릴 수 있다.

예상보다 깊은 경로로 예산이 넘치면 그 시도의 결과를 전혀 반영하지 않고,
연속된 primary 구간을 반으로 나눠 동일 seed로 다시 계산한다. 재시도 전에
실패한 pool을 해제한다. 물리적인 반사 상한을 낮추거나 Ray를 버리지 않는다.
다른 native 오류가 발생하면 확대된 batch도 원래의 안전한 크기로 나눠 기존
CPU 수송 경로로 복구한다. 예산 분할과 provider fallback은 별도로 집계한다.

반사 수식, FP64, `fastmath=False`, primary 난수 호출 순서, counter RNG,
광량 누적 순서, 독립 검산과 path 우선순위는 유지했다. CPU 실행은 CUDA를
import하지 않는 검사도 통과했다. UI와 서버 실행 방식은 변경하지 않았다.

## 기준과 환경

- 작업 폴더: `C:/Users/Administrator/Documents/TV leakage simulator main`.
- `main@157230c6`와 로컬 작업이다. 커밋/push, 서버 시작/재시작은 하지 않았다.
- Intel Core i7-10700, NVIDIA RTX 3070 8GB, driver 591.59, CUDA Toolkit 13.1.1.
- Python 3.13.3 x64, Numba 0.66.0, llvmlite 0.48.0.
- CPU 기준은 5B 입력 검증 개선 완료본의 wavefront/4 worker 경로다. 기존
  counter planner를 `157230c6`에서 읽어 별도 JIT cache로 실행한다. 후보는
  자동 통합 수송/8 worker다. 이전 단계의 광원 준비·광학 palette 개선도 포함한다.
- GPU 기준은 `157230c6`의 GPU tracing/summary/광원 준비 함수를 메모리에서
  교체한 경로다. 후보와 같은 소스 파일을 되돌리거나 CPU fallback을 GPU
  기준으로 사용하지 않았다.
- 같은 장치/장면/프로세스에서 순서를 교대해 첫 실행과 warm 3회를 기록했다.
  CPU/GPU benchmark는 동시에 실행하지 않았다. CAD import와 최초 BVH build는
  표에서 제외한다. benchmark 도중 source hash가 바뀌지 않았음을 확인했다.

## CPU 성능

| 장면 | Ray 수 | 기존 CPU | 현재 CPU | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 1.420초 | 0.410초 | 3.467배 |
| 확률 2회 반사 | 1,000,000 | 1.498초 | 0.517초 | 2.899배 |
| 갇힌 구조 상한 10 | 1,000,000 | 8.987초 | 4.205초 | 2.137배 |
| TV CAD 부하 대조군 | 100,000 | 1.597초 | 0.724초 | 2.205배 |
| 흡광 대조군 상한 20 | 100,000 | 0.130초 | 0.065초 | 2.020배 |
| 흡광 대조군 상한 100 | 100,000 | 0.140초 | 0.062초 | 2.245배 |
| 흡광 대조군 상한 1,000 | 100,000 | 0.298초 | 0.061초 | 4.874배 |

7조건 모두 공개 결과의 semantic hash가 bit-exact였고 native fallback은 0이었다.
이번 예산 분할은 정상 성능 측정에서 발생하지 않았다. 흡광 상한 1,000의 처리
chunk는 5개였고, 첫 2,048개 이후 최대 32,768개를 처리했다.

상한 100/1,000 대조군은 **실제로 첫 면에서 흡수되는 Ray**다. 10만 Ray가
모두 1,000번 반사했을 때 4.874배라는 의미가 아니다. 실제 1,000회 반사는
별도의 소규모 정확도 검사로 확인했다. TV 대조군은 50,944 triangle에서의
부하 검사이며 Receiver hit가 0이므로 실제 빛샘 Flux/Peak 검증으로 사용하지 않는다.

첫 Face 후보 실행은 14.179초, 새 compact 수송을 처음 사용한 상한 100 후보는
12.901초로 JIT 비용을 포함했다. 상한 1,000 첫 실행은 이미 같은 kernel이
준비된 상태였다. 첫 실행까지 2배 빨라졌다는 뜻이 아니다.

### 메모리 비용

예약 공간은 모든 primary에 1,001개 slot을 두는 방식으로 커지지 않지만,
작은 batch를 쓰던 기존 wavefront보다 scratch 예약량은 늘어난다. 상한 1,000
대조군의 tape/scratch 회계 peak는 기존 327,688 → 현재 100,110,480 bytes였다.
이는 전체 프로세스 RSS나 VRAM 실측치가 아니며 메모리 감소로 표현하지 않는다.
성공한 chunk의 pool capacity는 2,097,152 slot 이하였다. 경로 geometry를
기록하는 경우에는 slot당 byte 수가 더 크다.

## GPU 재검증

이번 변경은 CPU 수송 경로다. GPU resident/summary/intersection 세 모듈의 hash는
직전 광학 palette 검증본과 같았다. 아래 표는 현재 소스에서 다시 측정한 값이다.

| 장면 | 기존 GPU | 현재 GPU | 속도비 | CUDA 성공/시도 |
| --- | ---: | ---: | ---: | ---: |
| Face 직달 100만 Ray | 0.670초 | 0.256초 | 2.617배 | 16/16 |
| 확률 2회 반사 100만 Ray | 0.479초 | 0.261초 | 1.836배 | 16/16 |
| 갇힌 구조 상한 10, 100만 Ray | 0.445초 | 0.202초 | 2.207배 | 16/16 |
| TV CAD 부하 대조군 10만 Ray | 0.222초 | 0.166초 | 1.339배 | 2/2 |
| 흡광 상한 20, 10만 Ray | 0.049초 | 0.031초 | 1.604배 | 2/2 |
| 흡광 상한 100, 10만 Ray | 0.092초 | 0.048초 | 1.927배 | 7/7 |
| 흡광 상한 1,000, 10만 Ray | 0.541초 | 0.261초 | 2.076배 | 49/49 |

7조건 모두 `gpu_active`, CUDA 성공 1 이상, resident fallback과 CPU hybrid는 0이었다.
Face/Datum Plane emitter를 사용했다. GPU Face 첫 후보는 5.007초였다.
이산 결과 exact와 `1e-9` 물리량 허용 오차를 통과했지만 아래 엄격 반복 검사는
별도 기준으로 실패했다.

Source launcher의 사전 동기화와 production preflight를 수행했다. 공유 junction인
`.venv-gpu`는 삭제/재생성하지 않았다. OS 설치, 시스템 설정 변경과 재부팅도 없다.
기계 판정 기록은 다음과 같다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

## 정확도와 실패 복구 검사

- 자동 정책 통합 검사 245개가 142.547초에 통과했다.
- compact pool을 낮은 반사 상한에도 강제 적용한 CPU 검사 16개가 47.499초에
  통과했다. Specular/Lambertian/Gaussian/Mixed, threshold/Russian roulette,
  Receiver MIS, CAD face mapping, 저장 path와 signed-zero bit를 포함한다.
- pool을 64 slot으로 줄여 강제 overflow/분할을 유도해도 공개 결과가 기존
  CPU와 정확히 같았다. 실패한 scratch가 재귀 호출 전에 해제되는지도 검사했다.
- 흡광 Emitter 다음에 99.9% 경면 Emitter를 배치하고 2,048 slot 제한을 걸어도
  실제 최대 깊이 1,000을 유지하며 기존 CPU와 결과가 같았다. Ray 16개와 저장
  path를 사용한 정확도 검사이며 대규모 깊은 반사의 성능 증거는 아니다.
- 확대 batch에서 native 오류를 강제로 발생시켜도 원래 CPU batch로 분할 복구했고
  중복 집계 없이 결과가 같았다. 미사용 slot을 NaN으로 채운 packing 검사,
  read-only/독립 소유 검사와 CPU-only 새 프로세스 검사도 통과했다.
- `py_compile`, `git diff --check`가 통과했다.

### 엄격 GPU MIS 반복 검사는 미해결

8,192 Ray의 동일 MIS 입력을 CPU 기준과 8번씩 비교했다. 기존 GPU는 이번
실행에서 8/8, 후보는 7/8 통과했다. 후보 한 번의 `flux_squared_lumen2`가
34 ULP로 상한 32를 초과했다. 최대 상대 차이는 약 `4.479e-15`였고 이산 결과는
모두 같았지만 엄격 gate 실패를 통과로 처리하지 않는다.

직전 독립 검사에서는 기존 GPU도 상한을 넘은 적이 있다. 이번 CPU 변경이 GPU
kernel을 바꾸지는 않았으나, GPU atomic 합산 순서에 따른 제곱 광량/통계량의
반올림 위험은 별도로 해결해야 한다. 허용치를 넓히거나 분산을 0으로 만들지 않았다.
진단 script의 종료 코드만으로 판정하지 않고 JSON 각 `passed`를 확인했다.

## 다음 작업

GPU 제곱 광량과 통계 집계의 엄격 반복성을 먼저 안정화하고, 그 뒤 GPU의
bounded depth window/직접 summary 기록과 CAD 병목을 개선한다. BVH leaf를
줄이는 후보는 극미소 간격의 면 선택 차이가 있어 적용하지 않았다. 모든 후보는
같은 입력의 CPU 기준, 실제 CUDA 증거와 첫 실행/warm 교대 측정을 다시 통과해야 한다.

## 재현 자료

- CPU 수치/원시 시간: `outputs/perf5_cpu/actual-event-pool-million.json`.
- GPU 수치/실행 증거: `outputs/perf5_transport/actual-event-pool-gpu-million.json`.
- 기계 preflight: `outputs/perf5_transport/actual-event-pool-preflight.json`.
- 통합 검사: `outputs/perf5_transport/actual-event-pool-final-regression.log`.
- 강제 compact 검사: `outputs/perf5_cpu/actual-event-pool-forced-compact.log`.
- 엄격 반복 실패 포함 원본: `outputs/perf5_transport/actual-event-pool-roundoff.json`.
- 그림: `outputs/perf5_cpu/actual-event-pool-million.png`,
  `outputs/perf5_transport/actual-event-pool-gpu-million.png`.

수치 파일의 source hash와 benchmark 당시 `source_unchanged=true`를 함께 보존한다.
두 benchmark의 `passed=true`는 각각의 정합 기준에 대한 판정이며, 별도 32 ULP
반복 실패나 CPU·GPU 전체 2배 목표까지 통과했다는 뜻이 아니다.
