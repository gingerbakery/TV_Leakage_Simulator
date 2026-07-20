# PERF-1 Python Hot Path 최적화

## 작업 목적
- RT-2C에서 100만 ray 실행 시간이 길어지는 원인을 측정하고, 광학 계산 결과를 유지하면서 Python 실행 병목을 먼저 줄인다.
- CAD 교차 가속과 GPU 적용 전에 현재 엔진의 기준 성능을 고정한다.

## 변경 내용

### 경로 객체 생성 최소화
- 모든 ray마다 `RayHit`를 생성하지 않는다.
- 3D 표시를 위해 저장하는 제한된 ray path에만 `RayHit` 객체를 만든다.
- receiver hit는 가벼운 내부 후보 객체로 누적한 후 필요한 경우에만 변환한다.

### 광원 sampling batch 처리
- 직사각형 Datum/Reference plane emitter의 origin과 direction을 NumPy 배열로 batch 생성한다.
- face emitter와 polygon emitter는 기존 scalar 경로를 유지한다.
- 지원 여부에 따라 자동으로 빠른 경로와 기존 경로를 선택한다.

### 반복 수치 계산 감소
- receiver의 폭, 높이, 역수, acceptance cosine, grid 크기를 사전 계산한다.
- 반사 계산에서 반복되던 generic vector 함수 호출과 중복 normalize를 줄였다.
- Specular, Gaussian, Lambertian 방향 생성의 수치식은 기존 모델을 유지한다.

### optical property 캐시
- ray가 면에 충돌할 때마다 assignment 우선순위를 다시 검색하지 않는다.
- 실행 시작 시 모든 face의 최종 optical property를 한 번 계산하고 face index로 조회한다.
- 우선순위는 `Face Override > Part Assignment > Mesh Material > Default > Unassigned`를 유지한다.

### 성능 정보 출력
- 결과 metrics에 `_performance_summary`를 추가했다.
- 기록 항목:
  - 실제 backend
  - NumPy batch sampling ray 수
  - scalar sampling ray 수
  - optical face cache 수
  - 저장 path 수
  - 초당 ray 처리량
- Web Result에 `Ray rate`를 표시한다.
- Web UI 버전을 `v0.9.9`로 변경했다.

## 성능 결과

### 초기 기준
- Gaussian 100,000 ray: `5.126초`
- 처리량: 약 `19,508 ray/s`

### PERF-1 완료
- Specular 100,000 ray: `2.022초`, `49,453 ray/s`
- Gaussian 100,000 ray: `2.262초`, `44,204 ray/s`
- Lambertian 100,000 ray: `2.102초`, `47,570 ray/s`
- Gaussian 1,000,000 ray: `22.980초`, `43,515 ray/s`
- Gaussian 100,000 ray 기준 개선율: 약 `2.27배`

## 결과 정합성
- 전체 unit test `29개` 통과
- Gaussian 100,000 ray receiver flux:
  - 최적화 전 NumPy sampling 기준: `0.4782534957 lumen`
  - PERF-1 완료: `0.4782534957 lumen`
- Gaussian 1,000,000 ray receiver flux: `0.4783742890 lumen`
- ray 수 증가에 따라 통계 분포가 수렴하는 정상적인 결과를 확인했다.

## 생성 파일
- `src/leakage_simulator/fast_sampling.py`
- `scripts/benchmark_perf1.py`
- `outputs/perf1_benchmark/summary.json`
- `outputs/perf1_benchmark/perf1_throughput.png`
- `docs/performance-acceleration-plan.md`

## 검증 명령
```powershell
.\_tools\python313\python.exe -m unittest discover -s tests -p 'test_*.py'
.\_tools\python313\python.exe scripts\benchmark_perf1.py --million-rays
```

## 한계
- 현재 triangle 교차 판정은 기존 CPU mesh 구현을 사용한다.
- 실제 STEP/X_T mesh에서는 triangle 수가 많아질수록 교차 판정이 다시 가장 큰 병목이 된다.
- NumPy batch sampling은 광원 생성만 가속하며 ray-CAD intersection 자체를 가속하지 않는다.
- GPU 실행 경로는 아직 구현하지 않았다.

## 다음 단계
- PERF-2로 CAD intersection adapter와 BVH/Embree/Open3D 후보를 비교한다.
- GPU가 없는 PC는 `python_numpy_cpu` 또는 향후 `accelerated_cpu`로 자동 대체한다.
- PERF-2 결과가 목표 시간에 미달할 때 GPU 백엔드를 검토한다.
