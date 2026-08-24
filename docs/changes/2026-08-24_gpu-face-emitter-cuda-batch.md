# GPU Face Emitter Batch 및 CUDA BVH 연결

## 목적

CAD Face emitter를 GPU로 요청했는데도 `Face · CPU scalar`,
`CUDA batches 0/0`, `Provider python_cpu`로 끝나던 기능 결손을 제거한다.

## 구현 내용

### Face primary batch 생성

- 선택 Face의 유효 삼각형, 면적, normal을 한 번 준비한다.
- 면적 누적분포로 방출 삼각형을 batch 선택한다.
- 삼각형 내부 barycentric point와 Lambertian, Gaussian, Isotropic 방향을
  NumPy 배열로 생성한다.
- 각 ray는 다음 세 배열에서 같은 row를 유지한다.
  - `origins: float64[N,3]`
  - `directions: float64[N,3]`
  - `source_faces: int64[N]`
- 동일 seed의 반복 실행은 bit-identical primary batch를 만든다.

### CUDA BVH 연결

- `source_faces`를 `RayBatch.ignore_faces`로 전달해 방출 원본 삼각형의
  self-hit을 방지한다.
- Face primary wave는 ray 수가 8,192 미만이어도 small-wave CPU hybrid를
  우회하고 CUDA BVH를 직접 호출한다.
- 이후 반사 wave는 기존 정책대로 8,192 미만이면 Numba CPU hybrid를 사용할
  수 있다.
- 다음 진단값을 추가했다.
  - `face_batch_primary_ray_count`
  - `gpu_cuda_hybrid_bypass_count`
  - `gpu_cuda_hybrid_bypass_ray_count`

### 단회·다회 반사 데이터 보존

- single-bounce batch의 Receiver 후보, primary intersection, stored path가
  row별 source face를 사용한다.
- object wavefront와 SoA wavefront의 최초 `current_source_face`를 Face source로
  초기화한다.
- event tape를 `ordered_primary_event_tape_v3`로 올리고
  `initial_source_faces`를 저장해 stored ray path에도 방출 Face ID를 보존한다.

### UI 동작

- Face emitter를 GPU 비호환 목록에서 제거했다.
- GPU 선택 후 Face emitter 때문에 나타나던
  `일부 Emitter는 CPU로 실행됩니다` 사전 경고를 제거했다.
- `polygon_auto` emitter만 CPU scalar 호환 경고 대상으로 유지했다.

## 실행 판정

Face-only GPU 실행이 정상이라면 다음을 모두 만족해야 한다.

- `compute_execution_state`: `gpu_active` 또는 실제 후속 hybrid가 있는
  `gpu_mixed`
- `face_batch_primary_ray_count > 0`
- `gpu_cuda_gpu_success_count > 0`
- `scalar_primary_ray_count == 0` (유효 Face emitter만 있는 경우)
- 최초 CUDA batch의 `ignore_faces`가 각 ray의 source Face와 일치

`gpu_mixed`는 후속 작은 reflection wave가 CPU hybrid로 처리된 경우 정상일 수
있다. 실제 CUDA 성공 count가 0이면 GPU 성공으로 판정하지 않는다.

## 검증

- 전체 Python 회귀: `294 passed, 442 subtests passed`
- 전체 Frontend 회귀: `26 files, 154 tests passed`
- Frontend lint, TypeScript typecheck, production build: 통과
- 200,000-ray 합성 Face primary 생성 microbenchmark:
  - vectorized batch: `0.097514초`, 약 `2.051M ray/s`
  - legacy scalar: `1.124629초`, 약 `0.178M ray/s`
  - 생성 단계 speedup: `11.533x`
- 위 microbenchmark는 primary ray 생성만 비교한 로컬 CPU 측정이며 전체
  ray-tracing 또는 CUDA speedup 수치가 아니다.
- 실제 NVIDIA 장치에서의 CUDA 성공은 이번 로컬 mock 검증만으로 주장하지 않는다.
  회사 PC에서는 GPU preflight와 결과의 CUDA success count를 별도로 확인한다.

## 남은 범위

- `polygon_auto` emitter의 triangulation batch 생성은 후속 과제다.
- 후속 작은 reflection wave의 CPU hybrid는 성능 정책이며 Face 미지원 오류가
  아니다.
- Receiver heatmap의 희소성은 stored path 제한과 무관하므로 hit 수, grid 해상도,
  non-zero cell ratio를 별도 진단해야 한다.
