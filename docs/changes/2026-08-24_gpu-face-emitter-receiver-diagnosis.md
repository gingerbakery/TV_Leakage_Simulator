# Face Emitter GPU 미사용 및 Receiver 희소 분포 진단

## 상태 업데이트

- 2026-08-24 후속 구현으로 Face emitter의 vectorized batch 생성, row별
  source-face 보존, CUDA BVH 연결을 완료했다.
- 아래 `결론`과 `코드 근거`는 수정 전 증상을 기록한 진단 이력이다.
- 현재 CPU 실행의 Face emitter는 legacy scalar를 유지하지만,
  `compute_backend="gpu_cuda"`에서는 Face primary batch가 CUDA를 호출한다.
- `polygon_auto` emitter는 아직 CPU scalar다.

## 진단 대상

- 회사 PC에서 `NVIDIA GPU`를 선택하고 준비 완료 상태를 확인한 뒤 Ray Tracing을 실행함.
- 실행 전 경고에 `Emitter 1 · Face · CPU scalar`가 표시됨.
- 실행 결과에 `GPU requested`, `Provider python_cpu`, `CUDA batches 0/0`가 표시됨.
- Receiver Heatmap이 연속적인 분포보다 드문 점 형태로 보였음.

## 결론

진단 시점의 화면은 CUDA 장치나 커널이 실행 중 실패한 결과가 아니다. 당시 Face emitter가 GPU batch 대상이 아니어서 CUDA 호출 자체가 발생하지 않은 CPU-only 실행이었다.

- `CUDA batches 0/0`은 CUDA 시도 0회, 성공 0회를 뜻한다.
- 진단 시점에는 Face emitter와 `polygon_auto` emitter가 legacy scalar 경로를 사용했다.
- 당시 활성 Emitter가 Face 하나뿐이면 GPU를 선택했더라도 전체 해석이 `python_cpu`로 실행됐다.
- 따라서 이번 화면만으로 NVIDIA GPU, Driver 또는 CUDA Toolkit 고장을 판정할 수 없다.
- 다만 실제 사용 빈도가 높은 CAD Face emitter가 GPU를 사용하지 못하는 것은 GPU 기능 범위의 중요한 결손이다.

## 코드 근거

- 진단 시점 `src/leakage_simulator/fast_sampling.py`의 `supports_fast_virtual_plane_sampling()`은 Face와 `polygon_auto`를 명시적으로 제외했다.
- `src/leakage_simulator/raytracer.py`는 scalar query에서 GPU가 요청되면 `gpu_cuda_scalar_uses_python_cpu`를 기록하고 Python CPU 교차 계산을 수행한다.
- `docs/cad-intersection-backend-contract.md`는 Face primary sampling과 reflection이 legacy RNG stream을 공유하므로 현재 batch에서도 scalar로 남는다고 정의한다.
- `tests/test_gpu_backend_contract.py`는 Face-only GPU 요청이 `gpu_requested_cpu_only` 및 CUDA 미호출로 끝나는 것을 현재 계약으로 검증한다.

## Receiver Heatmap 진단

Receiver Heatmap은 시각화용 `stored_paths`가 아니라 전체 `receiver_grids[].flux_lumen` 누적값으로 생성된다. 따라서 `max_stored_paths` 때문에 Heatmap Ray가 필터링되지는 않는다.

점 형태 분포의 우선 의심 항목은 다음과 같다.

1. Receiver hit 수가 grid cell 수보다 작거나 비슷함.
2. Receiver 해상도가 높아 한 cell당 평균 hit 수가 부족함.
3. 실제 구조 차폐, Receiver normal/acceptance angle 또는 좁은 Gap 때문에 hit ratio가 낮음.
4. Peak 기준 자동 색상 범위로 낮은 flux cell이 배경색에 가깝게 표시됨.
5. 사용자가 Stop을 눌렀거나 자동 수렴 조건으로 요청 Ray보다 적게 처리됨.

예를 들어 `100 × 100` Receiver는 10,000개 cell을 가진다. Receiver hit가 1,000개라면 모든 hit가 서로 다른 cell에 들어가더라도 최대 10% cell만 밝아질 수 있다. 매끄러운 raw Monte Carlo Heatmap에는 일반적으로 cell당 충분한 hit가 필요하다.

## 로컬 검증

- GPU Face-only/mixed 실행 계약 및 multi-bounce Receiver 정합성 테스트: `11 passed, 15 subtests passed`.
- 10,000-ray Face emitter CPU 재현:
  - total rays: 10,000
  - Receiver hits: 10,000
  - Receiver grid hit count: 10,000
  - stored paths: 8
- 저장 경로가 8개뿐이어도 Receiver grid에는 10,000개 hit가 모두 누적되어, path 저장 제한과 Heatmap 집계가 독립임을 확인했다.

## 회사 결과에서 추가 확보할 값

Receiver 희소 분포를 확정 진단하려면 같은 run에서 다음 값을 함께 확보한다.

- Emitter 종류와 각 Emitter Ray 수
- `total_rays`, `receiver_hit_count`, hit ratio
- Receiver resolution과 크기
- `stopped_early` 및 실제 processed ray 수
- `compute_execution_state`, `compute_execution_reason`
- CUDA attempt/success 수와 CPU fallback/hybrid 수
- 저장한 `.bitsam` 파일 또는 결과 JSON

## 수정 우선순위

1. Face emitter를 deterministic batch로 생성하고 CUDA BVH 교차 경로에 연결한다.
2. GPU 실행 전 `GPU 처리 Emitter 수 / 전체 Emitter 수`를 표시한다.
3. GPU 처리 가능 Emitter가 0개면 GPU 실행 버튼을 비활성화하거나 명확한 CPU-only 전환 확인을 요구한다.
4. Result에 `Receiver hits / grid cells / 평균 hits per cell / non-zero cell ratio`를 표시한다.
5. Raw 정량값은 유지하면서 표시 전용 보간·평활화 모드를 선택적으로 제공한다.

## 판정 기준

- Datum/reference rectangular emitter에서 `gpu_cuda_gpu_success_count > 0`이면 CUDA 경로는 실제 실행된 것이다.
- 수정 전 Face-only 결과의 `CUDA batches 0/0`은 당시 구현 제한에 따른 CPU-only 실행이었다.
- 수정 후 정상 Face GPU 실행은 `face_batch_primary_ray_count > 0`,
  `gpu_cuda_gpu_success_count > 0`이어야 한다.
- CUDA 실행 실패는 일반적으로 시도 수가 존재하거나 별도 Driver/Toolkit/kernel reason code가 기록되어야 한다.
