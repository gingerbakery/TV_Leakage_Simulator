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

### PERF-3: 병렬화와 GPU 검토
- 상태: 보류
- 진입 조건:
  - PERF-2 후에도 목표 시간에 미달
  - 실제 사용 ray 수가 반복적으로 100만 이상
  - 회사 PC의 GPU/드라이버 배포 조건 확인

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
