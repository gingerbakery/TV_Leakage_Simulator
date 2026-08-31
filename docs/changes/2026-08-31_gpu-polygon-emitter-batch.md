# GPU Polygon Emitter batch support

## 변경 내용

- `polygon_auto` Reference Plane Emitter를 CPU scalar 전용 경로에서
  vectorized virtual-plane batch 경로로 전환했다.
- Polygon은 기존 scalar 구현과 동일한 fan triangulation을 사용하고,
  각 삼각형 면적에 비례해 Ray 시작점을 샘플링한다.
- 일반 source sampling과 Receiver-directed MIS가 동일한 Polygon origin
  sampler를 사용한다.
- GPU를 선택한 Polygon Emitter는 CUDA BVH batch를 사용할 수 있으므로
  기존 CPU compatibility 확인창을 제거했다.

## 검증 기준

- 동일 seed의 Polygon batch origin/direction이 반복 실행에서 동일하다.
- 모든 시작점이 작성된 Polygon 내부에 있고 법선 epsilon offset이 유지된다.
- 모의 CUDA provider 계약에서 `compute_execution_state=gpu_active`,
  `gpu_cuda_gpu_success_count>0`, `scalar_primary_ray_count=0`을 요구한다.
- 작은 Polygon batch는 사각 Datum Plane과 동일하게 hybrid CPU 임계값이
  적용될 수 있다. 실제 GPU 사용 여부는 결과의 CUDA 성공 batch로 판정한다.
