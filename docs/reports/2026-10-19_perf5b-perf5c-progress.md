# PERF-5B와 PERF-5C 가속 목표 진행 보고

작성: 2026-10-19

> 후속 최신 측정/검증은
> [`광원 준비와 GPU 정수 집계 개선`](2026-10-19_perf5c-primary-and-partitioned-summary.md)을
> 따른다. 아래는 해당 후속 구현 이전의 중간 이력이다.

**CPU와 GPU 각각 현재 버전 대비 최소 2배라는 목표는 아직 달성하지 못했다.**
5C의 기록 초기화·전송·메모리 접근을 개선했고, 반사 상한 1,000의 흡광 대조군은
GPU 약 2.16배를 확인했다. 일반 장면의 GPU 개선은 아직 약 1.01~1.08배에
그친다. 후속 CPU 통합 수송의 최종 자동 정책은 직달 2.02배, 확률 2회 반사
1.95배, 복잡 다회 반사 1.60배, TV 부하 대조군 1.63배였다.
5B/5C와 전체 목표를 완료 처리하지 않는다.

기존 5B 1차의 과거 CPU 대비 속도비를 이번 CPU 목표의 성과로 다시 계산하거나,
CPU 대비 GPU의 비율을 GPU 자체 개선 비율로 사용하지 않는다.

## 기준과 환경

- Source checkout: `C:/Users/Administrator/Documents/TV leakage simulator main`.
- main `157230c6` + 기존 5A/5B 로컬 작업을 출발점으로 삼았다. 커밋/push하지 않았다.
- CPU 기준은 5B 입력 검증 개선 완료본, GPU 기준은 이번 5C 수정 이전 코드다.
- Intel Core i7-10700, NVIDIA GeForce RTX 3070 8GB, driver 591.59.
- CUDA Toolkit 13.1.1, Python 3.13.3 x64, Numba 0.66.0, llvmlite 0.48.0.
- 읽기 전용 `setup_windows_gpu.bat` 점검 후 요구사항 marker 일치를 확인했다.
  `run_web_gpu.ps1 -PreflightOnly`로 의존성/프론트엔드 빌드와 production 검사를
  수행했다. 공유 junction인 `.venv-gpu`는 삭제하거나 재생성하지 않았다.
- OS driver/Toolkit 설치, 시스템 설정 변경, 재부팅과 서버 시작은 하지 않았다.

Production preflight는 다음 여섯 필드를 모두 충족했다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

## 이번 5C 구현

1. 일반 compact summary의 event 배열을 같은 깊이의 인접 Ray가 연속 주소를
   사용하도록 배치했다. 상세 geometry 경로는 기존 행 우선 배치를 유지한다.
2. compact 실행에서 설정된 최대 깊이 전체를 미리 초기화하지 않는다. 실제
   방문한 event의 필수 필드만 설정한다. 집계는 terminal 종류와 깊이로 계산한
   실제 event 수만 읽고, 필요한 event가 없으면 실패 처리한다.
3. GPU summary의 다수 작은 배열 전송을 정렬된 단일 buffer와 서로 겹치지 않는
   typed view로 묶었다. 누적 상태는 device에 유지하고 chunk의 transient 영역만
   초기화한다. 반환 snapshot은 독립된 read-only 소유 배열로 보존한다.
4. origin/direction/power/source/RNG 및 초기화 데이터를 하나의 input buffer로
   묶었다. 선택된 표시 path의 재추적에도 같은 입력 경로를 사용한다.
5. GPU BVH 탐색 stack의 배열 배치를 인접 Ray의 접근에 맞췄다. FP64 triangle
   교차식과 동률 face 선택 규칙은 바꾸지 않았다.
6. 각 반사마다 전역 active counter를 증가시키는 대신 terminal depth histogram을
   만들고 역누적으로 동일한 깊이별 방문 수를 복원한다. 정수 count와 전체
   primary 수의 일치도 검사한다.

FP64/fastmath=False, Ray 수, seed와 counter RNG lane, 광속, 물성, Aim, 종료 정책,
수광면 및 nit 계산 의미는 유지했다. 미사용 event slot의 오래된 face/NaN이
집계에 섞이지 않는 검사를 추가했고, 유효 event 손상 시 전체 chunk 재실행을
검증했다. 표시 path와 Receiver 우선 quota의 기존 회귀도 통과했다.

이 변경만으로 전체 5C가 끝난 것은 아니다. event 공간은 여전히 primary 수와
최대 반사 상한의 곱에 비례한다. 반사 상한 1,000에서는 100,000 Ray가 여전히
49개 chunk로 나뉘며, 고정 깊이 window와 chunk 사이 snapshot 감소가 남아 있다.

## 같은 프로세스에서 측정한 GPU 결과

원본 GPU 함수와 개선 함수를 같은 프로세스에서 번갈아 실행했다. GPU kernel과
workspace cache는 두 구현 사이에 분리했다. 각 구현의 최초 실행을 별도 저장하고
warm 3회 중앙값을 사용한다. CAD 준비, API 직렬화/전송과 화면 렌더링은 제외한다.

| 장면 | Ray 수 | 기존 GPU | 개선 GPU | 속도비 | CUDA 성공/시도 |
|---|---:|---:|---:|---:|---:|
| CAD 면광원 직접광 | 1,000,000 | 0.679182초 | 0.644522초 | 1.05배 | 16/16 |
| 확률 반사 2회 | 1,000,000 | 0.484318초 | 0.446963초 | 1.08배 | 16/16 |
| 갇힌 구조 10회 | 1,000,000 | 0.519837초 | 0.496599초 | 1.05배 | 16/16 |
| 샘플 TV 코너 CAD | 100,000 | 0.225484초 | 0.223492초 | 1.01배 | 2/2 |
| 흡광 구조·상한 20 | 100,000 | 0.049675초 | 0.041660초 | 1.19배 | 2/2 |
| 흡광 구조·상한 100 | 100,000 | 0.093272초 | 0.058022초 | 1.61배 | 7/7 |
| 흡광 구조·상한 1,000 | 100,000 | 0.602850초 | 0.279440초 | 2.16배 | 49/49 |

모든 개선 실행은 `compute_execution_state=gpu_active`, reason 없음, CPU hybrid
시도/성공/실패와 resident fallback 0회였다. 광원 유형은 첫 장면 `face`, 나머지
`datum_plane`이다. CUDA batch 수는 화면에 저장한 path 수가 아니라 실제 실행 수다.

TV 코너는 50,944 triangle의 연산 부하 대조군이다. 해당 배치의 Receiver hit는
0이므로 실제 TV 빛샘 정합이나 작은 Receiver의 통계 품질을 증명하지 않는다.

![GPU 중간 개선과 2배 목표](../../outputs/perf5_transport/progress-final-gpu.png)

CPU 계산을 사이에 끼운 실험에서는 GPU 비율이 더 크게 나온 조건도 있었다.
최종 표에는 GPU끼리 교대 측정한 결과를 사용했다. 서로 다른 실행 조건 중
가장 큰 속도비를 골라 성과로 보고하지 않는다. 최초 시간과 모든 warm 표본,
소스 hash 및 Git 기준 함수는 JSON에 보존했다.

## 5B 추가 실험과 판단

- batch 8,192/32,768/65,536 × worker 1/4를 4개 장면에서 비교했다.
- 큰 batch는 일부 직접광에서 시간을 줄였지만, 10회 반사에서 65,536 설정은
  약 9% 느려졌다. 기본 batch를 일괄 확대하지 않았다.
- 원본 면별 optical resolution cache도 비교했지만 대표 장면의 CPU 추가
  가속을 입증하지 못했다. 해당 제품 변경과 전용 테스트는 되돌렸다.
- 따라서 현재 CPU 2배 목표는 미달이다. 현재까지의 작은 준비 최적화를
  반복하는 것보다 반사별 Python/native 왕복과 배열 준비를 통합해야 한다.

## 정확도와 회귀

- 이번 최종 제품 상태에서 지정한 CPU/GPU 회귀 196개가 통과했다.
- GPU 후보와 원본은 7개 장면에서 모든 discrete 결과가 같고 기존 strict FP64
  abs/rel 1e-9 gate를 통과했다. 별도 CPU/GPU 100만 Ray 비교도 통과했다.
- 최종 GPU-only 실행은 저장된 동일 조건 CPU 결과와도 비교했다. Ray 수는
  직접광/확률 반사/갇힌 구조 100만, TV와 흡광 조건 10만이다.
- CPU-only fresh process, 기존 ordered 합산·독립 검산·실패 원자성,
  event tape·광학 물성·ROI·Aim과 source launcher 회귀를 포함했다.
- snapshot이 다음 chunk에 의해 바뀌지 않음, packed view 구간 비중첩,
  미사용 slot의 stale/NaN 무시, 유효 event 손상 시 CPU 재실행,
  terminal histogram의 깊이별 방문 수 복원을 검사했다.
- 측정 중 소스 hash 변경 없음. `git diff --check` 오류 없음.

### 기존 32 ULP gate의 미해결 항목

초기 5C 후보를 검사하면서 Lambertian bounce MIS의 기존 32 ULP 제한을 한 번
초과했다. 변경 전 GPU 함수를 복원해 같은 입력을 8회씩 재검사했을 때 원본과
초기 후보 모두 1회씩 33 ULP가 나왔다. 모든 discrete 결과는 같았고, 원본의
최대 상대 차이는 5.66e-15, 초기 후보는 4.35e-15였다.

이 자료는 해당 현상을 신규 변경만의 광선 누락으로 단정할 근거가 없음을
보여준다. GPU atomic 합산 순서의 영향인지, 엄격 gate를 어떤 오차 경계로
설계할지는 추가 검토한다. 테스트 허용치를 조용히 넓히지 않았다. 이 별도
MIS 반복 검사는 위 196개 회귀의 전체 통과 주장에 포함하지 않으며,
전체 정밀 검증 완료를 선언하지 않는다.

## 후속 5B: CPU 통합 수송과 독립 집계 검산

CPU의 기존 FP64 BVH 교차 kernel과 counter 반사 kernel을 한 native 수송 loop에서
호출한다. 반사마다 Python으로 돌아와 배열·객체를 다시 만드는 과정을 없앴다.
RNG key/lane, 수광면 우선 거리, 삼각형 tie-break, epsilon 이동, 종료 정책은
기존 kernel과 같은 계약을 사용한다. worker 출력은 primary별로 분리된다.

- 결과는 기존 read-only primary-major event tape로 변환하고 strict 검사를 한다.
  기존 ordered reducer, 중복 float shadow, 독립 float oracle, count 검산과 digest를
  제거하지 않았다. 수송에 실패하면 집계 전 해당 batch 전체를 기존 경로로 재실행한다.
- 독립 oracle의 입력 벡터는 기존 NumPy 변환으로 별도 산출한다. indexed add만
  재정렬 없는 CPU native loop로 바꿔 `np.add.at`와 uint64 단위로 대조했다.
- 동일 광량 packet의 제곱은 CPython `float ** 2`를 한 번 계산해 채운다.
  서로 다른 packet은 기존 scalar power를 유지한다. `x*x`로 대체하지 않는다.
- 일반 CPU 자동 경로의 batch는 32,768, 기본 worker 상한은 8이다. 논리 CPU 수의
  절반 및 기존 환경변수 override 제한을 그대로 따른다. GPU batch 정책은 바꾸지 않았다.
- 강제 통합 실험에서는 반사 상한 1,000의 흡광 대조군이 약 0.47배로 느려졌다.
  따라서 자동 통합 수송은 검증한 상한 32 이하로 제한한다. 상한이 더 크거나
  명시적 진단 경로이면 기존 wavefront를 사용한다. 반사 허용 횟수를 32로 줄인
  것이 아니라 실행 구현의 선택 범위만 제한한 것이다.

정책 선택 후보 실험은 같은 세션에서 첫 실행과 교대 warm 3회, 각 warm 중앙값을 비교했다.
기준은 5B 입력 검증 개선 완료본의 기존 8,192 batch/4 worker이며, 후보는 통합
수송+집계 개선과 32,768 batch/8 worker다. 후보/기준 모두 같은 CPU를 사용한다.

| 장면 | Ray | 기존 CPU | CPU 후보 | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 1.407초 | 0.691초 | 2.04배 |
| 확률 2회 반사 | 1,000,000 | 1.498초 | 0.715초 | 2.10배 |
| 갇힌 구조, 상한 10 | 1,000,000 | 9.060초 | 5.602초 | 1.62배 |
| TV corner CAD 부하 대조군 | 100,000 | 1.616초 | 0.922초 | 1.75배 |

네 장면의 수광, 광량, 히트맵, 기여도는 hash exact였다. TV 부하 대조군의
Receiver hit가 0이라는 기존 제한은 그대로이며 실제 TV 빛샘 정확도나 희귀 입사
성능을 증명하는 장면은 아니다. 상한 10의 갇힌 구조도 일반 복잡 모델 전체를
대표하지 않는다. 최소 2배 목표를 두 합성 장면의 통과로 완료 판정하지 않는다.

초기 통합만의 개선은 약 1.07~1.37배였다. 검산 비용 개선을 더한 8,192/4 후보는
약 1.41~1.63배였고, batch/worker 정책을 함께 조정한 값이 위 표다. 가속을 모두
BVH 연산 자체의 개선으로 해석하지 않는다.

추가 산출물:

- `outputs/perf5_cpu/fused-paired-million.json`: 통합 수송 1차 비교와 높은 상한의 회귀.
- `outputs/perf5_cpu/fused-indexed-paired-million.json`: 독립 oracle/packet 제곱 개선 비교.
- `outputs/perf5_cpu/fused-32k8-paired-million.json`: 위 표의 기준/후보와 source hash.
- `outputs/perf5_cpu/fused-regression-focused.log`: CPU 집중 25개 테스트 통과.
- `tests/test_perf5_cpu_transport.py`: tape float bits, 네 반사 모델, 두 종료 정책,
  primary/bounce MIS, seed 변경, 1,000 상한, 0 광량, 실제 CAD 면 ID/저장 path,
  원자적 재실행, fresh CPU process의 CUDA 미수입 검사.

### 자동 정책 최종 재측정

최종 코드를 고정한 별도 교대 측정은 다음과 같다. 위 후보 실험보다 더 높은
숫자만 골라 사용하지 않으며, 아래 결과를 최종 자동 정책의 기준으로 삼는다.
후보의 요청 모드는 `auto`이며 실제 일반 장면은 32,768/8, 높은 상한은 기존
batch와 wavefront를 사용했음을 결과 메타데이터로 확인했다.

| 장면 | 기존 CPU warm | 자동 정책 warm | 속도비 |
| --- | ---: | ---: | ---: |
| Face 직달, 100만 Ray | 1.436초 | 0.710초 | 2.02배 |
| 확률 2회 반사, 100만 Ray | 1.562초 | 0.800초 | 1.95배 |
| 갇힌 구조 상한 10, 100만 Ray | 9.347초 | 5.851초 | 1.60배 |
| TV CAD 부하 대조군, 10만 Ray | 1.618초 | 0.992초 | 1.63배 |
| 흡광 대조군 상한 20, 10만 Ray | 0.136초 | 0.087초 | 1.56배 |
| 흡광 대조군 상한 100, 10만 Ray | 0.134초 | 0.134초 | 1.00배 |
| 흡광 대조군 상한 1,000, 10만 Ray | 0.299초 | 0.303초 | 0.99배 |

일곱 장면 모두 semantic hash exact, provider fallback 0,
`source_unchanged=true`, `passed=true`였다. 상한 1,000은 약 1.3% 지연으로
기존 3% 회귀 허용범위 안이지만 가속 성과로 계산하지 않는다.

첫 Face 실행은 기존 CPU 5.950초, 통합 CPU 14.270초로 후보가 더 느렸다.
추가 JIT가 포함되므로 이 작업은 첫 실행/서버 재시작 직후의 속도 향상을
보장하지 않는다. 위 속도비는 같은 세션의 warm 중앙값이다.
통합 kernel의 시간은 `wavefront_cpu_transport_execute_sec`에 교차·수광 판정·
반사 계산을 합쳐 기록한다. 기존 `intersection_sec`만 보고 교차 비용이 사라졌다고
해석하지 않는다.

추가 CPU 및 실제 GPU 통합 회귀는 210개를 통과했다. 기존에 별도였던 bounce
MIS의 32 ULP gate도 이번 1회는 통과했지만 이전 반복 roundoff 위험이 해결됐다고
선언하지 않는다. GPU production launcher의 preflight를 다시 통과했고 서버는
시작하지 않았다. 최종 CPU 성능 측정은 GPU 회귀가 끝난 뒤 따로 실행했다.

- `outputs/perf5_cpu/fused-production-paired-million.json`: 최종 자동 정책 및 모든 첫/warm 시간.
- `outputs/perf5_cpu/fused-production-paired-million.png`: 최종 자동 정책 비교 그래프.
- `outputs/perf5_transport/fused-full-regression.log`: 210개 통합 회귀 통과.
- `outputs/perf5_cpu/fused-auto-policy-test.log`: 추가 자동 선택 정책 검사 1개 통과.
- `outputs/perf5_transport/fused-gpu-inventory.log`: 읽기 전용 외부 환경 점검.
- `outputs/perf5_transport/fused-gpu-preflight.log`: launcher preflight와 frontend build.

**최종 CPU 목표도 아직 미달이다.** 직달만 2배를 넘었고 확률 2회 반사는
재측정에서 1.95배였다. 다회 반사와 TV 부하는 약 1.6배이며 일반 GPU의 2배
조건도 아직 달성하지 못했다. 전체 목표, 5B와 5C는 계속 진행한다.

## 이어서 할 작업

1. 5B: 복잡 다회 반사에서 남은 kernel 호출·기록·검산 병목을 줄이고, 높은
   상한에도 실제 event 수에 비례하도록 기록 공간을 개선한다. 단순 장면 2배를
   전체 CPU workload 2배로 확대 해석하지 않는다.
2. 5C: 최대 반사 수 전체의 event 공간 대신 제한된 깊이 window 또는 직접
   summary 누적 경로를 구현해 높은 상한에서 batch가 작아지는 원인을 제거한다.
3. GPU summary의 전역 atomic 경쟁을 부분 집계/안정 병합으로 줄이는 후보를
   비교하고 MIS의 반복 roundoff도 함께 재검증한다.
4. 같은 기준·장면에서 CPU와 GPU 각각 2배 조건을 다시 판정한다. 일반 장면의
   미달을 흡광 대조군 하나의 통과로 대체하지 않는다.

## 재현과 로컬 산출물

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf5_transport.py `
  --reference-revision 157230c6 --rays 1000000 --cad-rays 100000 `
  --depth-rays 100000 --output outputs\perf5_transport\million-paired.json
```

- `outputs/perf5_transport/baseline.json`: 5C 착수 전 최초 CPU/GPU 기준.
- `outputs/perf5_transport/million-histogram.json`: CPU와 GPU를 함께 비교한 대규모 실행.
- `outputs/perf5_transport/progress-final-gpu.json`: 최종 GPU-only 교대 비교 및 저장 CPU 정합.
- `outputs/perf5_transport/progress-final-gpu.png`: 최종 중간 그래프.
- `outputs/perf5_transport/roundoff-diagnostic.json`: 원본/초기 후보의 32 ULP 반복 진단.
- `outputs/perf5_transport/regression-progress.log`: 196개 회귀 로그.
- `outputs/perf5_cpu/large-batch-sweep.json`: CPU batch/worker 확대 실험.

산출물은 Git 제외 경로이며, 현재 단계는 로컬 검증 중이다. 서버 재시작,
커밋, push와 배포는 하지 않았다. 전체 가속 목표는 계속 진행 상태다.
