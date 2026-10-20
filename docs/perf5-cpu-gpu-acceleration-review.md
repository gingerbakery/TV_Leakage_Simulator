# CPU와 GPU 추가 가속 검토안

작성: 2026-10-18

후속 구현: CPU 기준 측정과 5B 1차 적용은
[`1차 CPU 결과`](reports/2026-10-18_perf5a-perf5b-cpu.md), 입력 검증/집계 준비의
추가 개선은 [`5B 후속 결과`](reports/2026-10-18_perf5b-cpu-preparation.md)를 따른다.
아래 검토는 구현 착수 전의 조사 기록이다. GPU 2배 중기 제안 목표와 채택 기준은
[`성능 가속 계획`](performance-acceleration-plan.md)에 구분해서 기록했다.

## 권장 방향

CPU는 기존 Numba 계산을 멀티코어로 확장하고, GPU는 높은 반사 상한에 비례하는
기록 공간과 초기화 비용부터 줄인다. 이후 BVH 탐색 품질을 개선하고, NVIDIA
OptiX를 이용하는 RT 하드웨어 경로를 별도 후보로 비교한다. 작은 Receiver의
희귀 입사는 필요한 Ray 수를 줄이는 중요도 샘플링 개선으로 다룬다.

가장 먼저 PERF-5A에서 현재 버전의 대표 장면과 구간별 시간을 고정해야 한다.
이번 검토는 설치된 LT 문서와 소스 구조를 조사한 결과이며, 새 성능 측정이나
LT 실행 결과가 아니다. 제품 계산 코드는 변경하지 않았다.

## 1. 조사 기준

- 소스: `main`의 `157230c6`, source checkout.
- 조사한 작업 폴더: `C:/Users/Administrator/Documents/TV leakage simulator main`.
- 장치 조회: Intel Core i7-10700, 8코어/16논리 프로세서;
  NVIDIA GeForce RTX 3070, VRAM 8,192 MiB, driver 591.59.
- 설치된 LT: `D:/LightTools/lt.exe`의 ProductVersion은 `2026 Update 1`,
  FileVersion은 `2026.00.0.0`.
- LT는 실행하지 않고 설치된 Help, PDF, 파일 버전만 읽었다. 설치 파일만으로
  비공개 GPU 구현, 연산 정밀도, 정확한 가속 라이브러리를 확정할 수는 없다.
- 새 GPU production preflight와 RT benchmark는 수행하지 않았다. 장치 조회와
  과거 보고서는 현재 실행의 GPU 성공 증거를 대신하지 않는다.

## 2. LightTools 자료에서 확인한 내용

### 속도 수치를 비교할 때의 조건

사용자 제공 `LightTools 2026 신기능 알아보기.pdf` 2쪽은 하드웨어와 모델에 따라
GPU가 10~50배 이상 빨라질 수 있다고 설명한다. 예제는 비구면 렌즈 시스템의
1억 Ray를 CPU 약 158초에서 GPU 8초로 줄인 약 20배 결과다. 모든 모델에서
100배라는 보장은 아니다.

Keysight 공식 소개도 일반적인 관측 범위를 10~50배로 설명하며, 별도 예제로
RTX 5000 Ada의 62.5배 결과를 제시한다. 이는 LT의 해당 CPU 조건 대비 결과이지
우리 프로그램 대비 가속비가 아니다.

설치된 `GPU Example LED Lens.pdf`는 단순 점광원/렌즈/수광면의 2.5억 Ray를
Core Ultra 7 165H에서 약 8분, RTX 2000 Ada에서 13초, RTX 4090에서 7초,
RTX 4500 Ada에서 9초로 안내한다. 첫 실행 준비 비용 때문에 재실행해서
비교하고, 불필요한 Save Ray Data를 끄도록 설명한다.

### 문서로 확인되는 가속 원리와 한계

설치 도움말 `illumination/liug_4.5.27.html`은 GPU가 Accelerated Ray Trace와
Probabilistic Ray Split을 사용한다고 명시한다. GPU에는 RT 하드웨어가 있는
NVIDIA RTX가 요구된다. 다만 이 설명만으로 OptiX 사용을 단정하지 않는다.

`core/lug6.07.013.html`과 `illumination/liug_4.5.05.html`의 최대 100배 설명은
정확한 CAD 표면 교차 대신 근사 표면을 쓰는 Accelerated Ray Trace에 대한
내용이다. GPU 전환의 가속비와 구분해야 한다. GPU 속도/정확도 슬라이더는
그 표면 근사 수준을 조절한다.

`illumination/liug_4.5.28.html`과 `liug_4.5.29.html`은 지원 범위와 제한을
따로 명시한다. 예를 들어 현재 설치본은 GPU에서 일부 Receiver/filter,
scatter aiming, ray paths, 일부 물성과 소스를 제한한다. 기능 범위와 기록량을
맞추지 않은 시간 비교는 공정하지 않다. Volume source는 해당 GPU 제한 목록에
있다. Fully immersed source 지원과 별도 immersion 제한 표현은 문서 판본의
모호성이 있으므로 이 검토에서 지원 여부를 추가 추론하지 않는다.

우리 프로그램도 이미 삼각형 mesh를 해석한다. LT의 근사 표면 가속을 처음
도입할 때의 최대 100배를 그대로 재현할 수 있다고 예상하면 안 된다.

## 3. 현재 코드의 확인된 구조

| 항목 | 현재 상태 | 추가 검토 방향 |
|---|---|---|
| CPU 실행 | production auto는 batch + Numba 교차/반사/reducer를 요청하고, 불가하면 fallback | 가속 런타임 공급과 핵심 serial loop의 병렬화 |
| GPU 수송 | 한 CUDA thread가 한 primary의 교차·반사·종료까지 연속 계산 | GPU 상주 구현을 유지하면서 메모리/탐색 개선 |
| GPU 결과 집계 | 일반 summary는 GPU accumulator 사용 | 기록과 재읽기, atomic, 결과 복사 비용 감소 |
| GPU geometry 기록 | 표시할 일부 경로만 sparse retrace | scalar event 기록도 streaming으로 축소 |
| 교차 구조 | 자체 binary BVH와 FP64 triangle 교차 | SAH, leaf/layout, CPU/RTX 대체 후보 |
| 광원 생성 | CPU NumPy batch, Aim Area/Sphere 포함 | 생성 시간과 rejection 비율 측정 후 선택적 개선 |
| 작은 수광면 | primary MIS와 pure Lambertian bounce MIS 구현 | Gaussian/Mixed/차폐 구조의 통계 효율 확장 |

예전 PERF-3 또는 PERF-4A의 CPU 왕복 병목 설명은 당시 구현의 기록이다.
현재 GPU를 단순히 교차 계산만 GPU에서 하는 구현으로 설명하면 부정확하다.
이미 반사와 집계를 GPU로 옮긴 PERF-4B/C를 다시 신규 과제로 제안하지 않는다.

### CPU 핵심 루프

현재 Numba/llvmlite는 `requirements-gpu-cuda.txt`에만 고정되어 있고
`requirements-dev.txt`에는 없다. CPU용 실행기로 만든 환경에는 Numba가 없어
`numba_not_installed`와 Python fallback이 발생할 가능성이 있다. 따라서 사내
CPU가 느리다는 보고는 먼저 실제 provider 성공/실패 기록과 설치 환경을
확인해야 한다. Numba CPU 계산에 NVIDIA GPU나 CUDA Toolkit이 필요한 것은
아니다. CPU 가속용 의존성 묶음을 GPU 준비와 독립적으로 제공하는 것을 5B에
포함한다. 기존 CPU 사용자의 환경을 이번 검토에서 변경하지는 않았다.

`native_cpu_intersection.py`의 `intersect_kernel`은 `njit(nogil=True,
fastmath=False)`이지만 Ray 반복은 일반 `range`다. 반사 planner와 ordered
reducer도 serial 반복을 사용한다. `nogil=True` 자체가 멀티코어 실행을 뜻하지
않는다. 실제 전체 CPU 사용률은 NumPy와 다른 구간을 포함하므로 PERF-5A에서
측정하되, 이 핵심 루프에 멀티코어 확장 여지가 있는 것은 소스로 확인된다.

### 높은 반사 상한에서 발생하는 구조적 비용

`wavefront_event_tape.py`는 한 batch의 event slot을 2,097,152개로 제한하고
`batch_size × (max_depth + 1)`이 이를 넘지 않도록 batch를 줄인다.
이는 GPU VRAM 고갈을 막는 보호 장치다.

현재 공식으로 계산한 GPU 요청 batch 65,536개의 실효 크기는 다음과 같다.

| 최대 반사 수 | 실효 GPU batch | 1억 primary 처리의 최소 chunk 수 |
|---:|---:|---:|
| 10 | 65,536 | 1,526 |
| 20 | 65,536 | 1,526 |
| 50 | 32,768 | 3,052 |
| 100 | 16,384 | 6,104 |
| 300 | 4,096 | 24,415 |
| 1,000 | 2,048 | 48,829 |

표는 단일 연속 workload의 계산값이며 실행 시간 측정은 아니다. 여러 광원의
부분 batch가 있으면 chunk 수는 더 늘 수 있다.

compact workspace에서도 face, incoming/reflected/emitted power, status/lobe
정보는 Ray별 전체 depth 공간에 할당된다. `resident_kernel`은 실제 수송 전에
전체 depth slot을 초기화한다. 따라서 실제 Ray가 1~2회 후 끝나도 설정 상한이
높으면 불필요한 초기화와 작은 batch 비용이 발생한다. 단순히 메모리 보호
한도를 풀면 VRAM 문제가 재발하므로 기록 구조를 바꿔야 한다.

작은 batch가 항상 CPU로 실행된다는 뜻은 아니다. resident 경로의 작은 batch도
GPU 실행이 가능하며, 일부 host-roundtrip 경로에는 별도 8,192-Ray hybrid
임계값이 있다. 장치별 실행 증거를 따로 확인해야 한다.

### GPU 집계와 탐색

- summary accumulator는 많은 profile/반사/수광면 통계에 atomic 누적을 사용한다.
  동일 표면이나 동일 heatmap cell에 집중되는 장면의 contention은 측정 대상이다.
- 현재 accumulator는 chunk마다 `device_state` 수치 배열을 CPU로 복사한다.
  특히 큰 heatmap, 다수 Receiver, 작은 batch 조합에서 빈도를 줄일 여지가 있다.
- BVH는 가장 넓은 centroid 축을 기준으로 정렬한 뒤 절반으로 분할한다. 장면의
  실제 교차 비용을 고려하는 SAH 분할과 다르다.
- 현재 CUDA kernel은 자체 BVH를 직접 순회한다. OptiX/DXR 등 RT 코어를 호출하는
  경로는 제품 소스에서 확인되지 않았다. RTX GPU에서 CUDA를 실행한다는 사실만으로
  RT 코어의 전용 교차 가속을 이용하는 것은 아니다.

## 4. 제안하는 PERF-5 단계

| 단계 | 대상 | 구현 내용 | 완료 기준 |
|---|---|---|---|
| 5A 기준 측정 | 공통 | 대표 CAD, 반사 수, 물성, 광원, heatmap별 구간 시간과 자원 사용 측정 | cold 1회 + warm 3회, 입력/장치/실행 증거/정확도 기준 고정 |
| 5B CPU 가속 | CPU | GPU와 독립된 Numba 런타임 공급, 교차/반사 병렬 loop, worker별 부분 집계 | 실제 native 사용과 코어 수별 속도 향상, seed/면 번호/결과 보존 |
| 5C 기록 비용 제거 | GPU 중심 | 직접 summary 누적 또는 bounded depth window, 전체 depth 초기화 제거, CPU snapshot 빈도 감소 | 반사 상한만 높여도 batch가 급감하는 현상 완화, 엄격 정합 통과 |
| 5D 교차 탐색 개선 | CPU/GPU | binned SAH, leaf size/layout, 선택적 wide BVH, scene cache 정책 검토 | triangle 검사/탐색 횟수 감소와 end-to-end 시간 단축 |
| 5E 전용 RT 후보 | CPU/GPU | CPU Embree와 GPU OptiX를 독립 provider로 시험 | 미소 gap·면 ID 정확도와 실제 CAD 속도 gate 모두 통과 |
| 5F 목표 오차 가속 | 공통 | 기존 MIS 활용 조건 정리, Gaussian/Mixed와 차폐 경로로 확장 | 동일 Flux/공간 분포에서 목표 오차 도달 시간 감소 |

권장 순서는 5A 이후 5B와 5C를 각각 진행하고, 5D, 5E로 확장하는 것이다.
5F는 작은 Receiver/낮은 반사율 장면에서 이득이 클 수 있으므로 5A의 variance
측정에서 필요성이 확인되면 앞당긴다. 5E는 실험 경로로 비교하고, 먼저 통과한
5B/C/D의 이득은 기존 정확 경로에 독립적으로 반영한다.

### 5A 측정 계약

대표 장면은 단순 렌즈 성능과 TV 틈새 성능을 섞지 않도록 나눈다.

1. 단일/이중 거울, 직접광: 교차와 기본 throughput 대조군.
2. 기존 TV 코너 검증 CAD: 복잡한 triangle 탐색과 ROI/face override.
3. 갇힌 고반사 구조: 반사 상한 20/50/100/300/1,000.
4. 짧게 끝나는 흡광 구조: 실제 경로를 유지한 채 반사 상한만 바꾸어 기록 비용 분리.
5. 작은 Receiver, 부분 차폐, 다수 광원: 희귀 hit와 batch 분절 확인.
6. Aim Area/Sphere, Lambertian/Gaussian, 순수/혼합 반사: 광원 생성과 확률 모델 비용.

최초는 100k~1M Ray로 profile하고, 유망 후보만 10M 및 100M까지 확장한다.
모든 조합을 처음부터 100M으로 돌리지는 않는다.

조건에는 geometry hash/triangle 수, 물성/면 지정, seed, Ray 수, 반사 상한,
실제 평균/최대 반사, 종료 정책, 수광면 크기와 cell 크기, 기록량을 남긴다.
CPU는 요청한 provider와 실제 native 성공/fallback을 구분해 기록한다.
표시 path는 0과 500을 비교하고, summary와 상세 기여도를 분리한다. 광원
batch 생성에서 Aim 제안/채택 비율도 기록한다.

구간별로 CAD 준비/BVH build, primary 생성, 교차·반사, event 기록, 집계,
CPU/GPU 복사, sparse path 재추적, 결과 생성/응답/화면 표시를 분리한다.
Python profile과 CUDA event 시간, 필요할 때 Nsight Systems/Compute로 확인한다.
GPU 사용률 하나로 병목을 판정하지 않는다.

### 5B CPU 구현 주의점

교차와 반사 loop부터 병렬화한다. 현재 serial stack과 임시 배열은 worker별로
분리하고, seed는 emitter/전역 primary index/depth/lane에 고정한다. shared
heatmap에 여러 worker가 동시에 쓰지 않도록 지역 누적과 결정적인 병합을 쓴다.
합산 순서가 바뀌는 경우 bit-exact 주장 대신 현행 strict FP64 허용오차로 검증한다.

CPU 가속용 Numba/llvmlite 의존성을 GPU 환경 준비와 분리해 제공하고 실제
native 실행 여부를 확인한다. 이후 기본 후보는 Numba 병렬 kernel이다. C++ extension은
profile로 필요성이 확인되면 비교한다. Windows process 병렬화는 큰 CAD/mesh
복사와 시작 비용을 먼저 평가한다. GPU 세션의 CPU 보조 처리에는 과도한 worker를
쓰지 않아 장치 간 자원 경쟁을 막는다.

### 5C GPU 구현 주의점

일반 summary는 Ray별 모든 반사 기록 대신 필요한 통계를 바로 누적하거나,
고정된 8~32 depth window를 순차 처리하는 후보를 비교한다. 최종 depth별 통계와
표시 path는 보존하되 상세 기록 모드는 별도 유지한다. 깊이가 긴 Ray만 이어서
처리하는 active compaction은 현재 thread-resident 방식과 실제 장면별로 비교한다.

heatmap과 전역 합계는 GPU에서 run 전체 동안 유지하고 CPU에는 진행 상태와
일정 간격 snapshot 및 최종 결과만 보낸다. 실패/취소 시 완료 chunk 경계를
보존하여 재실행이 이미 누적된 Ray를 이중 계산하지 않게 한다.

표면/동일 cell atomic 집중에는 block/warp 부분 합산 후 병합을 시험한다.
GPU primary 생성, 비동기 전송, CUDA Graphs, launch 크기 조정은 측정된 비용이
클 때 추가한다. 기존 구현도 traversal과 반사 루프를 결합하므로, 커널 융합만
새로 하면 문제가 해결된다고 가정하지 않는다.

### 5D와 5E의 정확도 경계

5D는 기존 FP64 triangle 교차를 유지하며 BVH 분할과 layout부터 바꾼다.
SAH는 build 시간이 늘 수 있으므로 warm 수송뿐 아니라 CAD 준비 시간도 비교한다.
캐시 재사용 시 형상/ROI/transform/traceability 변경은 갱신하고 면 속성은
정확히 새 binding에 반영한다. ROI 밖 부품을 추가로 제외하는 최적화는 광학
경계 조건을 바꾸므로 단순 성능 개선에 섞지 않는다.

OptiX는 RTX의 전용 탐색/triangle 교차 가속을 사용할 수 있는 후보다. CPU
Embree도 SIMD와 최적화 BVH를 활용하는 후보지만, 기본 triangle/Ray API의
float 중심 표현은 우리 FP64 계약과 별개로 검증해야 한다.

FP32에서 찾은 최근접 triangle 하나만 FP64로 다시 계산해서는 누락된 다른
후보를 복구할 수 없다. 따라서 안전한 후보 열거를 위한 보수적 bounds, ROI
국소 원점, 원본 double origin/direction 유지, 후보별 FP64 재검사, 모호 구간의
기존 strict 경로 재실행을 함께 설계한다. 오차 한계가 성립하지 않거나 후보
누락을 막지 못하는 구간은 FP64 경로를 사용한다. 정밀 보완 비용이 커서 속도
이득이 사라지면 해당 provider를 기본 경로로 승격하지 않는다.

### 5F는 같은 물리 조건에서 필요한 표본을 줄이는 작업

현재 primary Receiver MIS와 pure Lambertian bounce MIS를 먼저 대표 TV 구조에
적용해 목표 오차 도달 시간을 비교한다. Gaussian/Mixed에는 해당 PDF와 MIS
weight를 구현한 뒤 확대한다. 다수/차폐 Receiver에서 무의미한 proposal이
늘지 않도록 전략 선택도 측정한다. Specular delta 경로는 별도로 유지한다.

Aim Area는 발광 분포 자체를 바꾸는 광원 설정이다. 속도를 높이기 위해 Aim이나
광량/반사율을 바꾸는 작업은 같은 물리 문제의 가속으로 계산하지 않는다.
가중 MIS의 수광 hit 수 증가는 독립적인 유효 표본 증가와 같지 않으므로 제곱합,
effective sample size, 여러 seed의 Flux 및 heatmap 분산을 함께 판정한다.
backward/bidirectional tracing은 광원 radiance/PDF와 nit 의미까지 확장해야 하는
후속 연구 항목으로 두며, 이번 초기 CPU/GPU 최적화에 포함하지 않는다.

## 5. 성능과 정확도 판정

시간은 두 가지로 비교한다.

- 같은 Ray 수/물리 입력의 총 실행 시간과 실제 교차 처리량.
- 같은 통계 품질의 결과를 얻는 시간. Flux 오차와 heatmap/Peak 영역의 오차를
  따로 확인하며, raw 최대 cell Peak 하나만 수렴 기준으로 쓰지 않는다.

선택 후보의 초기 승격 기준은 대표 장면 warm 중앙값 20% 이상 개선과 작은
대조군 3% 이내 회귀로 제안한다. 이는 기대 가속비가 아니라 채택을 판단할
기준이다. cold JIT/BVH/upload, warm, 최종 응답 시간을 따로 기록한다.
100M 결과를 주장하려면 실제 100M 실행과 장시간 VRAM/메모리 안정성 결과를 남긴다.

물리 설정은 다음을 동일하게 고정한다.

- 원본 면/부품 ID, ROI의 원본 면 연결, 재질과 face override 우선순위.
- 광원 위치/분포/Aim, 총 입력 광속, Receiver 범위와 수광각, cell 크기.
- 종료 threshold/Russian roulette, 반사 상한, 입사각 반사율 보정.
- Specular/Lambertian/Gaussian/Mixed의 모델과 파라미터.

검증은 해석해가 있는 직접광/반사율 감쇠, 작은 장면의 brute-force FP64,
현재 strict CPU/GPU 기준과 차례로 비교한다. 일반 장면은 face/hit/종료 count의
이산 정합과 현행 strict FP64 abs/rel 1e-9 계약을 적용한다. 거의 동률인 교차의
tie-break도 명시적으로 검증한다. 고반사/다회 반사에서는 끝에 남은 광속과
반사 상한 절단 손실을 별도 보고한다.

미소 gap, 큰 전역 좌표, 경계/꼭짓점 입사, 평행/중복/인접 면, grazing angle,
재질 override, transform, 여러 Receiver를 회귀 장면에 포함한다. 중요도 샘플링은
여러 독립 seed의 신뢰구간과 공간 분포로 검증한다. 단위 정합은 lm/lux를 우선
확인하고 nit 추정치의 기존 의미를 속도 개선 과정에서 바꾸지 않는다.

GPU 검증 보고에는 production preflight의 6개 필드, provider 계약,
`compute_execution_state`, CUDA 시도/성공, CPU hybrid/fallback, 광원 유형,
장치 이름과 cold/warm 시간을 남긴다. fallback된 실행은 GPU 가속 성과로 합치지 않는다.

## 6. 예상 효과와 다음 착수점

현재 자료로 CPU/GPU가 추가로 몇 배 빨라진다고 확정할 수는 없다. 기대 효과는
장면에 따라 CPU 멀티코어, 높은 반사 상한의 고정 기록 비용 제거, 복잡한 CAD
탐색 개선, 희귀 hit의 표본 수 감소에 나뉜다. RTX 교차 backend는 큰 후보지만
FP64 미소 gap 정합까지 유지한 이득이 확인되어야 한다.

다음 착수점은 PERF-5A다. 특히 같은 흡광 구조에서 반사 상한만
20→50→100→300→1,000으로 바꾸는 실험을 먼저 넣는다. 실질적인 광학 경로가
거의 변하지 않아도 시간 차이가 큰지 확인하면, 수송 계산과 기록 비용을
분리하여 5B/5C/5D의 우선순위를 수치로 결정할 수 있다.

## 근거 자료

### 제공 및 설치 자료

- `C:/Users/Administrator/Downloads/LightTools 2026 신기능 알아보기.pdf`, 2쪽.
- `D:/LightTools/ExamplesLibrary/GPU Example - LED Lens/GPU Example LED Lens.pdf`, 2~7쪽.
- `D:/LightTools/Help/wnew/RelNotes.1.17.html`.
- `D:/LightTools/Help/illumination/liug_4.5.27.html`, `liug_4.5.28.html`, `liug_4.5.29.html`.
- `D:/LightTools/Help/core/lug6.07.013.html`.
- `D:/LightTools/Help/illumination/liug_4.5.05.html`.

### 제품 소스 및 기존 실험

- `src/leakage_simulator/raytracer.py`: production auto 선택과 batch 상한 적용.
- `src/leakage_simulator/native_cpu_intersection.py`: serial Numba 교차 kernel.
- `src/leakage_simulator/native_cpu_counter_wavefront.py`: 반사 planner.
- `src/leakage_simulator/native_cpu_ordered_reducer.py`: serial ordered 집계.
- `src/leakage_simulator/geometry.py`: centroid midpoint BVH.
- `src/leakage_simulator/gpu_cuda_resident_wavefront.py`: GPU 상주 수송, depth slot 초기화.
- `src/leakage_simulator/gpu_cuda_summary_accumulator.py`: GPU summary 및 chunk별 CPU 복사.
- `src/leakage_simulator/wavefront_event_tape.py`: event slot 제한과 batch 공식.
- `docs/reports/2026-08-25_perf4c-gpu-accumulator.md`.
- `docs/reports/2026-08-25_perf4d-compact-workspace.md`.
- `docs/reports/2026-08-25_perf4e-b-lambertian-bounce-mis.md`.

### 공식 공개 자료

- [Keysight LightTools 2026 소개](https://www.keysight.com/blogs/en/tech/sim-des/lighttools-2026-advancing-optical-system-simulation-with-gpu-acceleration-visionsym-integration-and-stray-light-ar-waveguide-meta-optic-lens-and-cad-import-workflow-enhancements)
- [NVIDIA OptiX의 RTX 교차 및 실행 구조](https://developer.nvidia.com/blog/how-to-get-started-with-optix-7/)
- [Intel Embree의 CPU SIMD 및 BVH 라이브러리](https://www.embree.org/)
