# PERF-3B Batch Backend 계약

## 결과

최신 main `86eaa4b`를 기준으로 CAD 교차 성능을 측정하고, 향후 native CPU와
CUDA GPU가 공통으로 구현할 batch 입출력 계약을 추가했다.

이번 변경은 GPU 구현이나 실제 ray tracer batch 연결이 아니다. 최초
`intersect_rays()` 구현은 기존 scalar `intersect_ray()`를 row별 호출하는
CPU reference adapter이며 `native_batch=false`다. 이 단계에서 속도가
빨라졌다고 판단하지 않는다.

## 기준 성능

환경:
- Intel Core i7-10700, Python 3.13.3
- bundled right-bottom ROI STEP, 50,944 triangle
- seed `20260717`, warm flat BVH

50,000 ray를 5회 실행한 scalar BVH 결과:
- 중앙값 `2.3079초`
- `21,664.6 ray/s`
- cold import `0.9828초`
- BVH build `0.9147초`

계약 구현 후 재현 benchmark 결과:
- scalar BVH `22,864.8 ray/s`
- batch 256 CPU reference `20,079.1 ray/s` (`0.878x`)
- batch 4,096 CPU reference `20,451.3 ray/s` (`0.894x`)
- batch 50,000 CPU reference `20,271.0 ray/s` (`0.887x`)
- scalar/batch face 및 distance mismatch `0`
- 50-ray brute-force/BVH mismatch `0`

CPU reference batch의 약 10~12% 비용은 배열 row를 Python tuple로 바꾸고
결과 배열을 생성하는 현재 adapter 오버헤드다. 이 수치는 native/GPU
backend가 제거해야 할 비용을 포함한 의도적인 비교 기준이다.

사용자 로컬 `.bitsam`의 10,000-ray depth-10 smoke 결과:
- 중앙값 `2.5735초`
- `3,885.8 primary ray/s`
- receiver hit 1,276, surface hit 22,291
- 3회 결과 동일

로컬 `.bitsam`은 작업 전부터 존재한 미추적 사용자 파일이므로 읽기 전용
측정에만 사용했으며 fixture 또는 commit 대상으로 추가하지 않았다.

## 계약

입력 `RayBatch`:
- `origins`, `directions`: contiguous `float64 (N, 3)`
- `min_t`, `max_t`: contiguous `float64 (N,)`
- `ignore_faces`: contiguous `int64 (N,)`, `-1`은 제외 없음

출력 `RayHitBatch`:
- `t`: contiguous `float64 (N,)`
- `face_indices`: contiguous `int64 (N,)`
- miss: `(inf, -1)`
- 입력 row 순서 유지

point, normal, `TriangleFace`는 유효 hit에 한해 `materialize()`로 기존
`HitRecord`를 만든다. GPU 결과 전송량과 불필요한 Python 객체 생성을
줄이기 위한 결정이다.

거리 의미는 기존 scalar 경로를 보존한다.
- `min_t = max(1e-8, value)`
- `t > min_t`
- `t <= max_t`
- `max_t <= min_t`는 miss
- `trace_excluded` face 제외
- 같은 거리에서는 가장 작은 최종 mesh `face_index`

## 하위 호환성

- 기존 `TriangleMesh.intersect_ray()` 변경 없음
- 기존 `RayTraceConfig.intersection_backend` 값 `auto/brute_force/bvh` 유지
- 24 triangle 이하 brute-force, 25 triangle 이상 BVH 자동 정책 유지
- 프로젝트 JSON과 frontend schema 변경 없음
- ray tracing 실행 경로 변경 없음
- `gpu_cuda` 값은 실제 provider와 fallback이 준비될 때까지 공개하지 않음

## 검증 범위

- seeded random ray scalar/batch parity (`brute_force`, `bvh`)
- ray별 min/max/ignore와 exclusive/inclusive 경계
- miss sentinel, 빈 batch, 입력 shape/value 검증
- 같은 triangle tie, `trace_excluded`, chunk invariant
- materialized point/normal/triangle parity
- 입력 배열 불변
- 재현 가능한 PERF-3B benchmark script

## 다음 단계

1. NumPy primary ray가 이미 생성되는 virtual-plane fast path를 wavefront
   batch로 연결한다.
2. receiver distance를 per-ray `max_t`로 전달하고 row 순서/RNG 결과를
   보존한다.
3. intersection timing과 batch 통계를 `_performance_summary`에 추가한다.
4. native CPU prototype과 현재 scalar baseline을 비교한다.
5. prepared CUDA backend, capability probe, precision policy, whole-batch CPU
   fallback을 구현한다.

GPU 단계 전 확인할 위험:
- cached mesh의 run별 backend 설정을 immutable per-run context로 분리
- GPU primitive id를 최종 mesh face index로 remap
- `ignore_face`를 closest-hit 후가 아니라 traversal 중 제외
- 약 1,000 mm 좌표와 `epsilon_mm=1e-4` 조합의 float32 정밀도 검증
- Stop 반응성을 위한 적정 batch 크기 결정
- GPU 실행 실패 시 부분 결과를 섞지 않고 전체 batch CPU 재실행
