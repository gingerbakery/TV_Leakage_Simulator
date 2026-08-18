# Ray Tracing 성능 가속 계획

## 목적
- 계산 정확도와 데이터 계약을 유지하면서 반복 설계에 필요한 실행 시간을 줄인다.
- GPU가 없는 PC에서도 프로그램 전체 기능을 사용할 수 있도록 CPU 경로를 항상 유지한다.
- 특정 가속 라이브러리에 종속되지 않도록 계산 백엔드를 단계적으로 교체한다.

## 백엔드 계층

### 1. CPU 기준 경로
- 이름: `reference_cpu`
- 역할: 결과 정합성 검증, 개발 디버깅, 가속 라이브러리가 없는 PC의 안전한 대체 경로
- 특징: 순수 Python 기반으로 가장 이식성이 높지만 대형 CAD와 많은 ray에서 느리다.

### 2. 최적화 CPU 경로
- 이름: `python_numpy_cpu`
- 현재 기본 경로
- 적용 내용:
  - 가상 평면 광원의 NumPy batch sampling
  - 저장 대상 ray path에만 `RayHit` 객체 생성
  - receiver 좌표계와 판정 상수 사전 계산
  - face별 optical property 사전 캐시
  - 반사·산란 벡터 계산의 Python 호출과 중복 정규화 감소

### 3. CAD 교차 가속 경로
- 예정 이름: `accelerated_cpu`
- 현재 prototype: runtime-only `numba_cpu` opt-in
- 적용 후보:
  - 자체 BVH
  - Intel Embree
  - Open3D `RaycastingScene`
- 목적: 실제 STEP/X_T에서 생성된 수십만~수백만 triangle에 대한 ray-scene intersection 병목 제거
- 원칙: 교차점의 `face_index`, 거리, 위치, normal이 현재 데이터 계약과 동일해야 한다.

### 4. GPU 경로
- 예정 이름: `gpu_cuda`
- 적용 후보:
  - NVIDIA OptiX
  - CUDA 기반 custom kernel
- 목적: 대량 ray와 다중 반사 계산의 처리량 확대
- 조건: 지원 GPU, 드라이버, CUDA runtime 또는 배포 가능한 GPU 실행 환경 필요

## GPU가 없는 PC의 동작
- 프로그램을 사용할 수 있다.
- CAD import, 3D viewer, ROI, Transform, Material, Emitter, Receiver 기능은 GPU ray tracing 지원 여부와 무관하게 동작한다.
- ray tracing은 자동으로 CPU 백엔드를 선택한다.
- 차이는 주로 ray tracing 실행 시간이다.
- 동일한 설정에서 CPU와 GPU 결과는 허용 오차 범위 내에서 동일한 통계 경향과 에너지 합계를 유지해야 한다.
- GPU 전용 기능 때문에 프로젝트 파일을 열 수 없거나 결과를 확인할 수 없는 구조는 허용하지 않는다.

## 자동 선택 정책
1. 사용자가 특정 백엔드를 강제로 지정한 경우 해당 백엔드의 사용 가능 여부를 확인한다.
2. GPU 실행 환경이 정상이라면 `gpu_cuda`를 선택한다.
3. CPU 교차 가속 라이브러리가 있으면 `accelerated_cpu`를 선택한다.
4. 그 외에는 `python_numpy_cpu`를 선택한다.
5. 실행 실패 시 한 단계 낮은 백엔드로 안전하게 대체하고 결과에 실제 사용 백엔드를 기록한다.

위 항목은 최종 목표 정책이다. PERF-3B-2 prototype 시점의 실제 `auto`는
기존 Python CPU를 유지하며 optional provider를 probe하지 않는다. Native CPU
또는 GPU는 정합성, 실제 ROI warm 성능, cold start와 배포 크기 gate를 모두
통과한 뒤에만 자동 선택 대상으로 승격한다.

## 단계

### PERF-1: Python hot path 최적화
- 상태: 완료
- 범위:
  - 객체 생성 최소화
  - NumPy 광원 batch sampling
  - optical property 캐시
  - 반사·receiver 수치 계산 단순화
  - 반복 가능한 100만 ray benchmark

### PERF-2: CAD intersection 가속
- 상태: 1차 완료
- 우선순위:
  1. brute-force reference와 flat BVH 결과 정합성 테스트 완료
  2. 사전 계산 triangle + flat BVH CPU backend 연결 완료
  3. TV 샘플과 9,486 triangle STEP 성능 비교 완료
  4. 실제 회사 TV ROI 도면의 end-to-end 측정 필요
  5. 필요 시 Embree/Open3D adapter를 후속 비교

### PERF-3: batch 병렬화와 GPU

#### PERF-3A: 단일 반사 fast path
- 상태: 완료
- 가상 평면 emitter의 NumPy sampling과 depth 0~1 전용 경로를 적용했다.

#### PERF-3B-0: 기준 측정과 batch 계약
- 상태: 완료 (2026-08-18)
- 최신 main `86eaa4b`에서 scalar BVH micro/end-to-end 기준을 측정했다.
- `RayBatch`, `RayHitBatch`, `TriangleMesh.intersect_rays()` 계약을 추가했다.
- 최초 구현은 기존 scalar 교차를 row별 호출하는 CPU reference adapter다.
- `RayTraceConfig`와 실제 ray tracing 실행 경로는 아직 변경하지 않았다.
- 이 단계 자체는 속도 향상이 아니라 이후 backend의 정합성 기준이다.

#### PERF-3B-1: wavefront batch 연결
- 상태: 완료 (2026-08-18)
- NumPy primary ray가 이미 준비되는 virtual-plane fast path를 연결했다.
- receiver 거리를 ray별 `max_t`로 전달하고 primary/secondary ray를 각각 batch query한다.
- 결과 누적과 stored path는 원 primary row 순서로 commit해 scalar 결과를 exact 보존한다.
- 기존 65,536 sampling batch와 기본 4,096 intersection chunk를 분리했다.
- Stop/progress는 시작한 intersection chunk를 원자적으로 완료한 뒤 경계에서 처리한다.
- face/polygon emitter와 `max_depth >= 2`는 기존 scalar 경로를 유지한다.
- runtime dispatch/chunk 인자는 프로젝트 파일에 저장하지 않는다.
- PERF-3B-1 완료 시점의 교차 구현은 Python row-loop CPU reference이며
  `native_batch=false`였다.
- 따라서 기본 `auto`는 scalar를 유지하고 reference batch는
  테스트/benchmark에서만 명시적으로 요청했다.

#### PERF-3B-2: native CPU prototype
- 상태: prototype 완료 (2026-08-18), 기본 자동 선택은 보류
- strict-float64 Numba BVH provider를 runtime-only opt-in으로 연결했다.
- provider import/JIT, immutable scene pack, capability와 whole-query fallback을
  기존 Python CPU 경로와 분리했다.
- 실제 50,944-triangle CAD intersection micro는 독립 실행에서 약
  `48.98~50.45x`였지만, 100,000-ray synthetic end-to-end는
  `0.961~1.009x`의 baseline 수준이어서 자동 선택 gate `1.20x`를 통과하지
  못했다.
- 기본 `auto`는 Numba를 probe/import하지 않고 기존 scalar 경로를 유지한다.
- 기존 PERF-3B-1과 같은 scalar workload를 교대 13회 측정한 결과 runtime
  중앙값 차이는 `+0.42%`로 3% 회귀 gate 안의 측정 잡음 수준이었다.
- 측정된 Numba/llvmlite module directory 약 149.8 MiB와 cold JIT 비용 때문에
  lightweight package에는 아직 포함하지 않는다.

#### PERF-3B-2A: multi-bounce wavefront와 후처리 batch
- 상태: 다음 단계
- 사용자가 체감하는 `max_depth=10`, stored paths, 고해상도 Receiver workflow를
  primary/secondary 한 단계에 제한되지 않는 depth wavefront로 전환한다.
- Receiver 판정, reflection planning, grid/contribution commit의 Python row-loop
  비중을 줄인다.
- 동일한 wavefront와 compact active-ray buffer를 CPU native와 CUDA가 함께
  사용하게 해 GPU backend가 교차 커널만 빠르고 전체 실행은 느린 상태를
  방지한다.

#### PERF-3B-3: CUDA GPU backend
- 상태: 예정
- prepared mesh/device buffer를 여러 batch가 재사용하는 adapter를 구현한다.
- 지원 여부, 정밀도, upload/kernel/download 시간을 각각 기록한다.
- GPU가 없거나 초기화/실행에 실패하면 batch 전체를 CPU BVH로 다시 실행한다.
- GPU primitive id와 최종 mesh face index의 remap을 보존한다.

## 정합성 기준
- 동일 seed와 동일 백엔드에서는 결과가 재현되어야 한다.
- 백엔드가 달라 난수열이 달라지는 경우 receiver flux와 hit ratio를 통계 허용 오차로 비교한다.
- 에너지 증가가 발생해서는 안 된다.
- face/component/material id 연결이 가속 전후 동일해야 한다.
- 성능 개선 때문에 optical assignment 우선순위가 달라져서는 안 된다.

## 현재 측정
- 장면: RT-2C 단일 평면 반사 synthetic scene
- Python: 3.13.3
- Gaussian 100,000 ray:
  - 초기: `5.126초`
  - PERF-1: `2.262초`
  - 개선: 약 `2.27배`
- Gaussian 1,000,000 ray:
  - PERF-1: `22.980초`
  - 처리량: 약 `43,515 ray/s`
- 실제 CAD에서는 triangle 수에 따라 교차 계산 비중이 크게 증가하므로 PERF-2 효과가 더 중요하다.

## PERF-2 측정
- TV 샘플 116 triangle:
  - 기존 recursive BVH: 약 `21,767 ray/s`
  - flat BVH: 약 `38,983 ray/s`
  - 개선: 약 `1.79배`
- Helical Gear 9,486 triangle:
  - brute-force: 약 `219 ray/s`
  - 기존 recursive BVH: 약 `4,972 ray/s`
  - flat BVH: 약 `19,099 ray/s`
  - 기존 BVH 대비 약 `3.84배`
- reference mismatch: `0`

위 PERF-2 TV 수치는 과거 116 triangle tessellation 기준이다. 최신 adaptive
tessellation에서는 full STEP이 106,352 triangle이므로 직접적인 전후 비교
기준으로 사용하지 않는다.

## PERF-3B 진입 기준 측정 (2026-08-18)

측정 환경:
- CPU: Intel Core i7-10700, 8 core / 16 thread
- Python: 3.13.3
- 기준 commit: `86eaa4b`
- seed: `20260717`

교차 micro baseline:
- CAD: `tv_leakage_roi_right_bottom_no_gap.stp`
- triangle: 50,944
- warm scalar flat BVH, 50,000 ray, 5회 중앙값
- 실행 시간: `2.3079초`
- 처리량: `21,664.6 ray/s`
- cold import: `0.9828초`
- BVH build: `0.9147초`
- brute-force reference mismatch: `0`

실제 저장 프로젝트 smoke baseline:
- 활성 ROI triangle: 45,167 / 50,944
- datum-plane Lambertian emitter, depth 10, 10,000 ray, 3회 중앙값
- 실행 시간: `2.5735초`
- 처리량: `3,885.8 primary ray/s`
- receiver hit: 1,276
- surface hit: 22,291
- 세 실행의 결과가 동일했다.

프로파일링에서는 Python BVH traversal, 특히 ray-AABB 판정이 교차 시간의
대부분을 차지했다. 따라서 batch 경계를 고정한 뒤 native/GPU traversal로
교체하는 개발 순서가 타당하다.

계약 구현 후 동일 조건 재현 benchmark:
- scalar BVH: `22,864.8 ray/s`
- CPU reference batch, size 256: `20,079.1 ray/s` (`0.878x`)
- CPU reference batch, size 4,096: `20,451.3 ray/s` (`0.894x`)
- CPU reference batch, size 50,000: `20,271.0 ray/s` (`0.887x`)
- scalar/batch face mismatch: `0`
- scalar/batch distance mismatch: `0`
- brute-force/BVH mismatch 50-ray sample: `0`

현재 adapter는 Python row loop와 배열 변환/결과 할당 비용 때문에 scalar보다
약 10~12% 느리다. 이는 예상된 reference 비용이며 성능 개선으로 계산하지
않는다. 이후 native/GPU 구현은 같은 계약과 mismatch `0`을 유지하면서 이
기준을 넘어야 한다.

## PERF-3B-1 wavefront 측정 (2026-08-18)

장면:
- RT-2C Gaussian 단일 반사 synthetic scene
- 100,000 primary ray, 200,000 CAD intersection query
- stored path OFF, summary contribution
- 3회 실행 중앙값

결과:
- scalar dispatch: `2.7691초`, `36,112 primary ray/s`
- batch 256: `3.9080초`, `25,588 ray/s`, `0.709x`
- batch 4,096: `3.8938초`, `25,682 ray/s`, `0.711x`
- batch 65,536: `3.9964초`, `25,022 ray/s`, `0.693x`
- receiver/surface/terminated/flux 전체 exact 일치
- semantic mismatch: `0`

PERF-3B-1 측정 당시 batch dispatch는 sampler의 ray별 generator/yield를
없애고 chunk 단위
dispatch 경계를 만들었지만 Receiver/plan/commit과 `intersect_rays()` 내부는
`python_cpu` reference의 Python scalar 처리를 사용했으므로 end-to-end로는
최선의 4,096 chunk도 scalar 대비 처리량이 약 28.9% 낮고 실행시간은 약
40.6% 길다. 이는 PERF-3B-2 native kernel이 제거해야 할 dispatch overhead
기준이다. 현재 후보 중 기본 4,096 chunk가 가장 빨랐으며 Stop 응답성과
향후 GPU launch 비용을 함께 고려해 유지한다.

## PERF-3B-2 native CPU 측정 (2026-08-18)

환경:
- Windows 10, Python 3.13.3
- Numba 0.66.0, llvmlite 0.48.0
- strict `float64`, `fastmath=false`, serial native kernel
- 동일 seed와 frozen ray 배열, 3회 warm 실행 중앙값

실제 CAD intersection micro:
- CAD: `tv_leakage_roi_right_bottom_no_gap.stp`
- triangle: 50,944, ray: 100,000
- Python scalar BVH: `4.4195초`, `22,627 ray/s`
- native scalar 호출: `1.0627초`, `94,101 ray/s`, `4.16x`
- native batch 256: `0.1173초`, `852,575 ray/s`, `37.68x`
- native batch 4,096: `0.0907초`, `1,102,531 ray/s`, `48.73x`
- native batch 65,536: `0.0876초`, `1,141,467 ray/s`, `50.45x`
- face mismatch: `0`, distance bit mismatch: `0`

준비 비용:
- 기존 BVH build: `0.9063초`
- immutable native scene pack: `0.0706초`
- 최초 JIT compile: `1.5052초`
- 최초 native execute: `0.0858초`
- 최초 native cold wall: `1.9288초`

100,000-ray 단일 반사 synthetic end-to-end(모든 case에서 비교를 위해
`intersection_backend="bvh"` 강제):
- Python scalar: `3.1828초`, `31,419 primary ray/s`
- native scalar: `4.6694초`, `0.682x`
- Python reference batch: `4.4098초`, `0.722x`
- native batch 4,096: `3.1531초`, `1.009x`
- receiver/surface/flux semantic mismatch: `0`

별도 `--no-write` 독립 재실행에서는 최대 CAD micro `48.98x`, native batch
end-to-end `0.961x`였다. 즉 wall-time 변동을 포함해도 교차 kernel은 약
49~50배였지만 전체 파이프라인은 baseline 수준이며 `1.20x` gate와는 충분히
떨어져 있다.

교차 커널의 큰 개선이 전체 실행에서 바로 나타나지 않은 이유는 이 synthetic
장면의 교차가 매우 싸고 Receiver 계산, reflection plan, Python 객체 복원과
row-order commit이 실행시간 대부분을 차지하기 때문이다. 더 중요한 실제
사용 조건인 `max_depth >= 2`는 아직 PERF-3B-1 wavefront 대상이 아니므로 이번
단계만으로 “백만 ray 수 분” 문제를 해결했다고 판단하지 않는다.

따라서 native provider는 명시적 개발/benchmark opt-in으로 유지한다. 자동
선택 승격 조건은 실제 ROI end-to-end mismatch `0`, warm 성능 최소 `1.20x`,
기본 Python CPU 회귀 3% 이내, cold start와 배포 크기 수용이다. 현재 optional
module directory 크기는 Numba 약 33.2 MiB, llvmlite 약 116.6 MiB로 합계 약
149.8 MiB다. dist-info, 추가 native library와 archive overhead를 포함한 실제
배포 증가는 이보다 조금 클 수 있다.

별도 수동 기본 CPU 회귀 검증은 동일 프로세스에서 PERF-3B-1 parent와 현재 코드를
순서 교대해 각각 13회 확인했다. 100,000-ray scalar 중앙값은 parent
`2.7708초`, 현재 `2.7824초`였고 runtime 차이는 `+0.42%`, paired mean 차이는
`+0.03%`였다. 결과 payload는 exact 일치했고 Numba import/probe/JIT/native
scene pack은 한 번도 발생하지 않았다.
