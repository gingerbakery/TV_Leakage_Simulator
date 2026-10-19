# PERF-5C GPU 제곱 광량 합산 순서 안정화

후속 Receiver Flux 통합 집계와 최신 성능은
[`Receiver 광량 합산 안정화`](2026-10-19_perf5c-ordered-receiver-flux.md)를 따른다.
아래는 그 이전 단계의 기록이다.

2026-10-19 source checkout 검증 결과다. **CPU 7개 조건은 기존 대비 warm
2.122~4.846배였고, GPU 주요 합성 장면 3개는 2.017~2.448배였다.** GPU CAD는
1.345배이며 작은 흡광 대조군도 일부 목표 미달이다. CPU·GPU 전체 2배 목표와
PERF-5C 전체 구현은 아직 완료되지 않았다.

기존 실패를 재현하던 seed의 엄격 검사는 24/24 통과했지만, seed를 확장하자
다른 Flux 합산 항목 3개가 32 ULP 상한을 넘었다. **제곱 광량의 순서는 고쳤으나
모든 GPU float 집계의 엄격 반복성이 해결된 것은 아니다.**

## 원인 분리

같은 Receiver packet을 저장해 원래 순서의 합계와 `math.fsum`을 각각 계산했다.
기존 GPU는 packet 값보다 atomic 합산 순서에서 큰 차이가 발생했다. 8,192 Ray
사례에서 CPU의 순차 합 자체도 `fsum`과 26 ULP 달랐다. 단순히 합계를 더 높은
정확도의 값으로 교체하면 CPU 기준과의 엄격 정합을 보장할 수 없는 이유다.

GPU packet을 CPU의 원래 순서로 더하면 제곱 광량 총합이 CPU와 일치했다.
이에 수송·반사·난수는 그대로 두고 Receiver별 제곱 광량 총합만 고정 순서로
계산하도록 변경했다. 과거의 수십 % 광량 감소 문제를 이 수 ULP 차이로
설명하는 것은 아니다. 이번에 다룬 것은 부동소수점 집계의 미세한 순서 차이다.

## 구현

### GPU 안에서 순서 유지

GPU가 이미 보유한 terminal kind/Receiver/power 배열을 직접 읽는다. 임시
primary별 복사 버퍼를 만들거나 전체 packet을 CPU로 내려받지 않는다.
Receiver마다 warp 하나가 32개 packet을 함께 읽고, lane 순서대로 제곱 광량을
더한다. 같은 Receiver의 primary 순서와 chunk 사이의 기존 합계를 유지한다.

128-primary tile에 같은 제곱 광량만 있으면 그 값과 개수만 사용한다. 동일 값의
반복 덧셈은 일정한 ULP 간격 구간에서 IEEE 비트 증가량으로 계산하되, 실제
덧셈으로 tie-to-even을 먼저 처리하고 지수 경계를 넘기 전에 멈춘다. `n*x`로
재결합하거나 Kahan 합계로 바꾸는 방식이 아니다. 중간 결과가 원래 반복 덧셈과
같아야 하며, 697개 조건에서 signed zero, half-ULP tie, 지수 경계, subnormal,
100만 회 덧셈 및 overflow의 비트 결과를 독립 CPU loop와 비교했다.

반사 수식, FP64/`fastmath=False`, source sampling, counter RNG, Ray 수와 반사
상한은 바꾸지 않았다. 새로운 계약은
`device_primary_ordered_receiver_second_moment_v1`이다. 일반 Flux와 셀별 일부
집계는 여전히 기존 atomic 경로이므로 전체 결과의 bit-exact를 뜻하지 않는다.

추가 GPU workspace는 기본 65,536 batch/Receiver 1개에서 8,704 bytes다.
metadata slot은 131,072개 이하로 제한하고, 마지막 부분 batch에서도 미사용
구간을 읽지 않는다. borrowed terminal 배열은 기존 resident workspace에 속하며
추가 예약량에 중복 포함하지 않는다. 기존 summary의 host 복사량 제한도 유지했다.

### CPU 기록 적용 범위 조정

CPU의 실제 event 기록 pool은 이제 반사 상한 16 이상에서 자동 사용한다.
상한 20에서 첫 면에 모두 흡수되는 경우 dense stride 비용이 확인됐기 때문이다.
상한 15 이하는 기존 dense 기록을 유지한다. 두 방식의 물리 결과와 소유권,
실패 복구 계약은 같다.

실제로 모든 Ray가 상한까지 반사되는 10만-Ray 긴 복도도 별도로 비교했다.

| 실제 반사 상한 | Dense | Compact | Compact 시간 변화 |
| --- | ---: | ---: | ---: |
| 16 | 0.728초 | 0.731초 | +0.35% |
| 20 | 0.896초 | 0.900초 | +0.47% |
| 32 | 1.435초 | 1.456초 | +1.52% |

각 조건의 surface hit는 `Ray 수 × (상한+1)`이고 semantic hash가 정확히 같았다.
첫 면 흡광에서만 확인한 최적화가 깊은 실제 경로를 줄이지 않는지 확인한 검사다.
위 세 조건의 변화는 기존 3% 회귀 기준 안이었다.

## 성능 재측정

환경은 i7-10700, RTX 3070 8GB, driver 591.59, CUDA Toolkit 13.1.1,
Python 3.13.3 x64, Numba 0.66.0, llvmlite 0.48.0이다. 같은 장면/장치/프로세스에서
순서를 교대해 첫 실행과 warm 3회를 기록했다. CPU/GPU benchmark는 동시에
실행하지 않았다. 아래는 warm 중앙값이며 import/최초 BVH build/UI 시간은 제외한다.

CPU 기준은 5B 입력 검증 개선 완료본의 wavefront/4 worker, 후보는 자동 통합
수송/8 worker다. GPU 기준은 `157230c6`의 GPU 함수를 읽어 고정한 경로다.
각 장치의 기준을 따로 비교하며 CPU 대비 GPU 배수와 섞지 않는다.

### CPU

| 장면 | Ray 수 | 기존 CPU | 현재 CPU | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 1.397초 | 0.408초 | 3.421배 |
| 확률 2회 반사 | 1,000,000 | 1.461초 | 0.520초 | 2.812배 |
| 갇힌 구조 상한 10 | 1,000,000 | 8.874초 | 4.181초 | 2.122배 |
| TV CAD 부하 대조군 | 100,000 | 1.593초 | 0.732초 | 2.175배 |
| 흡광 상한 20 | 100,000 | 0.133초 | 0.054초 | 2.490배 |
| 흡광 상한 100 | 100,000 | 0.133초 | 0.059초 | 2.276배 |
| 흡광 상한 1,000 | 100,000 | 0.291초 | 0.060초 | 4.846배 |

7개 모두 기존 CPU와 semantic hash bit-exact, native fallback 0이었다. Face 첫
후보는 14.134초, compact kernel이 처음 사용된 상한 20 후보는 12.879초였다.
첫 실행의 JIT 비용은 여전히 크다.

### GPU

| 장면 | 기존 GPU | 현재 GPU | 속도비 | CUDA 성공/시도 |
| --- | ---: | ---: | ---: | ---: |
| Face 직달 100만 Ray | 0.655초 | 0.268초 | 2.448배 | 16/16 |
| 확률 2회 반사 100만 Ray | 0.449초 | 0.211초 | 2.124배 | 16/16 |
| 갇힌 구조 상한 10, 100만 Ray | 0.462초 | 0.229초 | 2.017배 | 16/16 |
| TV CAD 부하 대조군 10만 Ray | 0.223초 | 0.166초 | 1.345배 | 2/2 |
| 흡광 상한 20, 10만 Ray | 0.048초 | 0.031초 | 1.569배 | 2/2 |
| 흡광 상한 100, 10만 Ray | 0.098초 | 0.052초 | 1.865배 | 7/7 |
| 흡광 상한 1,000, 10만 Ray | 0.613초 | 0.280초 | 2.191배 | 49/49 |

모든 정상 측정은 `gpu_active`, CUDA 성공 1 이상, resident fallback/CPU hybrid 0,
`cpu_gpu_deterministic_batch_v1`이었다. Face/Datum Plane emitter를 사용했다.
Face GPU 첫 후보 실행은 5.284초였다. 이산 exact와 `1e-9` 물리량 허용 오차는
통과했지만 GPU 전체 bit-exact나 아래의 모든 32 ULP gate 통과를 뜻하지 않는다.

흡광 상한 1,000은 첫 면에서 흡수되는 대조군이지 모든 Ray가 1,000번 반사한
성능이 아니다. TV 대조군은 50,944 triangle의 부하 검사이며 Receiver hit가
0이므로 Flux/Peak 정확도 검증에 사용하지 않는다.

같은 최신 코드에서 atomic 진단 모드와 순서 고정 모드도 별도로 교대 측정했다.
순서 고정의 시간 변화는 Face +1.83%, 확률 2회 +4.07%, 갇힌 구조 +6.49%,
CAD -1.08%였다. 정확도 안정화를 공짜 가속으로 설명하지 않는다. profiling으로
추가 동기화한 stage 시간은 이 end-to-end 차이에 단순 합산하지 않는다.

## 검증과 남은 실패

통합 검사 **254개가 145.535초에 통과**했다. GPU 오류 주입 시 batch 전체 복구,
다중 Receiver의 독립 순서 합계, 3개 chunk/마지막 부분 batch의 상태 동기화와
read-only snapshot, 기존 32 KiB summary 복사량 제한을 포함한다.
`py_compile`과 `git diff --check`도 통과했다.

| 엄격 비교 | 결과 | 판정 |
| --- | ---: | --- |
| seed 7171, 8,192 Ray, 24회 | 후보 24/24, 기준 GPU 21/24 | 후보 최대 26 ULP, 상한 32 유지 |
| seed 7169, 8,192/65,536 Ray, 각 4회 | 6/8 | 다른 Receiver Flux 항목 36 ULP 두 번 |
| seed 7173, 8,192/65,536 Ray, 각 4회 | 7/8 | 다른 Receiver total Flux 항목 33 ULP 한 번 |

추가 16조건에서 제곱 광량 총합 자체는 순서 고정 oracle과 모두 0 ULP였다.
남은 세 실패는 일반 Receiver total/depth Flux 합산이다. 최대 상대 차이는 약
`6.13e-15`이고 이산 결과는 같지만 엄격 gate 실패를 무시하지 않는다. 전체
40조건을 40/40 통과했다고 보고하지 않는다. 남은 Flux atomic 순서도 계속
조사해야 하며 LT 정합성은 별도다.

진단 도구는 후보 검사가 비어 있거나 하나라도 실패하면 실패로 종료하도록
수정했다. `passed`의 범위는 production 후보이고 기존 GPU의 통과 수는 따로
기록한다. 조사용 packet diagnostic은 각 JSON `parity.passed`를 직접 확인한다.

## 제외한 후보

- CPU로 모든 Receiver packet을 내려받아 순서 집계하는 후보는 135,744 bytes로
  기존 32 KiB 복사량 제한을 넘어서 제거했다. 현재 기본 경로는 GPU 내부 집계다.
- GPU 중간 packet 버퍼를 따로 두던 후보도 제거했다. 이미 있는 terminal 배열을
  직접 참조하고, device view를 매번 만드는 비용도 피한다.
- BVH leaf 순서를 유지한 loop tiling 2/4/8은 CAD에서 의미 있는 가속을 보이지
  않아 제품 코드에 적용하지 않았다. tree/교차 수식/면 선택 계약은 바꾸지 않았다.

## 실행과 자료

`main@157230c6`와 로컬 작업이다. 읽기 전용 inventory와
`run_web_gpu.ps1 -PreflightOnly`를 수행했으며, 공유 junction `.venv-gpu`는
삭제/재생성하지 않았다. OS 설치/설정 변경, 서버 재시작, 커밋/push는 하지 않았다.
production preflight는 다음 값으로 통과했다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

주요 원본은 다음과 같다. CPU/GPU 및 모드 비교 파일의 source hash와
`source_unchanged=true`를 보존한다.

- `outputs/perf5_cpu/moments-direct-million.json`
- `outputs/perf5_cpu/filled-medium-depth-layout.json`
- `outputs/perf5_transport/moments-direct-gpu-million.json`
- `outputs/perf5_transport/moments-direct-roundoff.json`
- `outputs/perf5_transport/moments-direct-seed-7169.json`, `moments-direct-seed-7173.json`
- `outputs/perf5_transport/moments-direct-mode-paired.json`
- `outputs/perf5_transport/moments-direct-full-regression.log`
- `outputs/perf5_transport/leaf-loop-tiling.json`

그림은 `outputs/perf5_cpu/moments-direct-million.png`와
`outputs/perf5_transport/moments-direct-gpu-million.png`다. 다음 단계는 남은
Flux 합산의 엄격 반복성, GPU bounded depth window/직접 summary와 CAD 교차
병목 개선이다. 일부 장면의 2배만으로 전체 목표 완료를 선언하지 않는다.
