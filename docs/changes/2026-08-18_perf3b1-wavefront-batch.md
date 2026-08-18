# PERF-3B-1 Wavefront Batch 연결

## 결과

PERF-3B-0에서 정의한 `RayBatch`/`RayHitBatch` 계약을 실제 단일 반사
ray tracing 경로에 연결했다. NumPy fast sampling을 지원하는 virtual-plane
emitter와 `max_depth <= 1` 조합에서 primary와 secondary CAD 교차를 각각
chunk 단위로 dispatch할 수 있다.

현재 `TriangleMesh.intersect_rays()`는 기존 scalar 교차를 row별 호출하는
CPU reference adapter다. 따라서 이번 단계의 목표는 즉시 가속이 아니라
RNG, 광속, Receiver grid, contribution, stored path를 바꾸지 않는 wavefront
실행 경계와 측정 기준을 고정하는 것이다.

느린 reference adapter가 일반 사용자 실행을 퇴행시키지 않도록 기본
`intersection_dispatch="auto"`는 scalar를 유지한다. Batch 경로는 테스트와
benchmark에서 `intersection_dispatch="batch"`로 명시한 경우에만 사용한다.
향후 provider가 `native_batch=true`일 때만 `auto` 선택 대상으로 올린다.

## 실행 구조

1. 기존 sampler가 동일하게 65,536개 단위 NumPy ray 배열을 만든다.
2. 배열을 별도의 기본 4,096-ray intersection chunk로 자른다.
3. ray별 direct Receiver 거리로 primary `max_t`를 구성한다.
4. primary CAD 교차를 한 번의 batch API 호출로 제출한다.
5. 원 row 순서대로 reflection RNG 결정을 만들고 생존 ray만 compact한다.
6. secondary CAD 교차를 batch로 제출한다.
7. Receiver, 통계, contribution, stored path를 원 primary row 순서대로
   commit한다.

Sampling batch와 intersection chunk를 분리한 이유는 NumPy sampler의 batch
크기를 바꾸면 같은 seed에서도 난수 draw grouping과 ray stream이 달라지기
때문이다. 기존 65,536 값을 보존하고 교차 제출 크기만 바꾸면 동일 ray를
정확히 비교할 수 있다.

face/polygon emitter와 `max_depth >= 2`는 reflection RNG의 기존 depth-first
소비 순서를 지키기 위해 계속 scalar 경로를 사용한다. 한 실행에서 batch와
scalar emitter가 섞이면 실제 dispatch는 `mixed`로 기록한다.

## Stop과 결과 원자성

Stop은 다음 sampling batch를 만들기 전과 intersection chunk 시작 전에
확인한다. 이미 시작한 chunk는 primary, secondary와 결과 commit까지 완료한
뒤 다음 chunk를 시작하지 않는다. 기본 설정의 최대 중단 지연 단위는 4,096
primary ray다. 부분 결과의 `total_rays`, progress와 intersection 통계는
commit이 완료된 chunk 기준으로 일치한다.

결과 JSON에는 batch 배열이나 miss sentinel이 노출되지 않는다.
`json.dumps(result.to_dict(), allow_nan=False)`가 성공하도록 유지했다.

## 성능 계측

`metrics._performance_summary`에 다음 필드를 추가했다.

- `requested_intersection_dispatch`
- `intersection_dispatch`: `scalar`, `batch`, `mixed`
- `intersection_batch_count`
- `intersection_batch_max_size`
- `intersection_ray_count`
- `intersection_scalar_query_count`
- `intersection_sec`
- `intersection_timing_scope="batch_dispatch_only"`
- `native_batch=false`

Scalar hot path에는 ray별 고해상도 타이머를 넣지 않았다. 200,000 query에
타이머를 두 번씩 호출하면 기존 scalar 경로가 10% 이상 느려지는 문제가
측정됐기 때문이다. 현재 `intersection_sec`는 batch API 호출 시간만 합산하며
전체 비교는 별도 benchmark의 end-to-end wall time을 사용한다.

## 최종 benchmark

환경과 조건:

- Windows 10, Python 3.13.3
- RT-2C Gaussian 단일 반사 synthetic scene
- 100,000 primary ray, 200,000 CAD intersection query
- stored path off, 동일 seed, 3회 실행 중앙값

| Dispatch | 중앙값 | 처리량 | Scalar 대비 |
| --- | ---: | ---: | ---: |
| Scalar | 2.7691초 | 36,112 ray/s | 1.000x |
| Batch 256 | 3.9080초 | 25,588 ray/s | 0.709x |
| Batch 4,096 | 3.8938초 | 25,682 ray/s | 0.711x |
| Batch 65,536 | 3.9964초 | 25,022 ray/s | 0.693x |

모든 batch 크기에서 receiver hit 100,000, surface hit 100,000, terminated 0,
Receiver flux `0.4782534957131787 lm`가 scalar와 exact 일치했고 semantic
mismatch는 0이었다.

최선의 reference batch는 scalar보다 처리량이 약 28.9% 낮고 실행시간은 약
40.6% 길다. 아직 Python tuple materialization, Receiver/plan/commit의 scalar
처리와 row-loop 교차가 남아 있어 예상된 결과다. 이 수치는 성능 향상으로
계산하지 않으며 PERF-3B-2 native kernel이 넘어야 할 기준선이다.

재현 명령:

```powershell
python scripts/benchmark_perf3b1_wavefront.py --rays 100000 --repeats 3 --batch-sizes 256 4096 65536
```

원시 결과는 git-ignored `outputs/perf3b1_wavefront/summary.json`에 기록된다.

## 검증

- scalar와 batch의 동일 seed 전체 semantic payload exact 비교
- chunk 1, 7, 64, 1,024 결과 및 제출 ray stream 불변
- Russian roulette, reflection summary와 stored path 순서 보존
- face emitter scalar fallback과 virtual-plane/face 혼합 실행
- primary 이후 Stop이 발생해도 secondary와 commit을 완료하는 chunk 원자성
- 잘못된 dispatch와 batch 크기 검증
- strict JSON serialization
- 신규 PERF-3B-1 테스트 11개 통과
- 전체 Python 테스트 123개 통과

## 다음 단계

PERF-3B-2에서 같은 계약 뒤에 native CPU intersection provider를 연결한다.
Python BVH traversal과 ray-AABB 호출을 제거하고 scalar 기준보다 실제로 빠른지
확인한 뒤, 동일 provider 경계에 CUDA device buffer 재사용, capability probe,
정밀도 정책과 whole-batch CPU fallback을 추가한다.
