# PERF-3B-2A Multi-Bounce Wavefront

## 결과

`max_depth >= 2` ray tracing을 depth별 compact active-ray wavefront로 실행할
수 있는 runtime opt-in 경로를 추가했다. 실제 45,167-triangle ROI 프로젝트의
100,000-ray, depth 10, stored-path 500 반복 측정에서 권장 1,024-ray Numba
wavefront는 중앙값 `7.0649초`, p95 `7.3970초`였다. 재측정한 legacy Python
scalar `26.1930초` 대비 `3.71x`, Numba native scalar 기준 대비
`1.59~1.62x` 빠르다. 1,000,000 ray 단순 선형 환산은 약 `70.7초`이므로
이번 단계만으로 최종 속도 목표를 달성했다고 판단하지 않는다.

## 활성화 범위

다음 조건을 모두 만족할 때만 multi-bounce wavefront를 사용한다.

- runtime `intersection_dispatch="batch"` 명시
- NumPy fast sampling을 지원하는 datum/reference virtual-plane emitter
- `max_depth >= 2`

`max_depth <= 1`의 explicit batch는 기존 PERF-3B-1 single-bounce 경로를
사용한다. 기본 `intersection_dispatch="auto"`는 legacy scalar를 유지한다.
`intersection_provider="auto"`도 Python CPU를 그대로 사용하고 Numba를
import하거나 capability probe하지 않는다.

Face emitter와 polygon-auto emitter는 primary sampling과 reflection이 같은
legacy RNG를 공유하므로 explicit batch 요청에서도 scalar로 실행한다. 따라서
GPU·Numba가 없는 PC, 기존 프로젝트와 face/polygon workflow의 기본 동작에는
변화가 없다.

## Depth wavefront

각 primary chunk는 다음 순서로 처리한다.

1. Primary index, origin, direction, energy, 이전 face와 depth를 state로 만든다.
2. 같은 depth의 Receiver 후보와 row별 CAD `max_t`를 NumPy batch로 계산한다.
3. Active ray만 `RayBatch`로 compact해 Python 또는 Numba provider에 전달한다.
4. Miss, Receiver 도달, reflection 종료 ray를 제거하고 생존 ray만 다음 depth로
   넘긴다.
5. Chunk가 모두 끝나면 원 primary index 순서대로 Receiver grid,
   optical/reflection/contribution summary와 stored path를 commit한다.

Ordered commit은 depth별 처리 순서 때문에 부동소수점 누적이나 bounded stored
path의 교체 순서가 달라지는 것을 방지한다. Active ray 수와 batch 수는 depth별
metric으로 기록해 실제 bounce 생존 분포를 확인할 수 있다.

## RNG와 정합성 계약

Legacy multi-bounce scalar는 한 emitter RNG를 `primary 0 depth 0..N`, 다음
primary 순서로 소비한다. True depth wavefront는 모든 active primary의 같은
depth를 먼저 처리하므로 stochastic draw 순서를 그대로 유지할 수 없다.

PERF-3B-2A는 emitter seed와 emitter 내부 primary index에서 독립 reflection
stream을 만드는 `per_primary_seeded_v1`을 사용한다.

- Specular/none과 threshold 종료처럼 random draw가 없는 경로는 legacy scalar와
  Receiver grid, flux, contribution, reflection summary, stored path가 exact하다.
- Lambertian, Gaussian, mixed와 실제 Russian-roulette draw가 있는 wavefront는
  같은 seed에서 chunk 크기, 반복 실행과 Python/Numba provider가 달라도 exact
  재현된다.
- Stochastic wavefront와 legacy scalar는 서로 다른 Monte Carlo realization이다.
  두 경로는 개별 ray/grid exact가 아니라 여러 seed의 Receiver flux, hit ratio,
  error estimate와 에너지 보존을 이용한 statistical parity로 비교한다.

성능 결과에는 다음 경계를 명시한다.

- `wavefront_reflection_rng="per_primary_seeded_v1"`
- `wavefront_rng_scalar_parity="exact_no_draw_statistical_stochastic"`
- `wavefront_stochastic_primary_ray_count`

구조 후보 A/B를 비교할 때는 같은 seed뿐 아니라 같은 dispatch를 사용해야 한다.

## Stored path 최적화

Stored path는 시각화용 bounded collection이다. Quota가 비었으면 완료 경로를
materialize하고, quota가 찬 뒤에도 Receiver 경로가 기존 dead-end를 교체할 수
있으면 materialize한다. 그 외 경로는 `_store_completed_path()`가 즉시 버릴
객체이므로 `RayHit` 목록 자체를 만들지 않는다.

실제 workload의 동일 4,096 chunk에서 최적화 전 `8.8017초`가 최적화 후
`7.4763초`로 약 `15.1%` 줄었다. 최종 권장 1,024 chunk까지 적용한 전체
중앙값은 `7.0649초`다.

- 완료 primary path: `100,000`
- materialized path: `931`
- materialization skipped: `99,069`
- configured stored-path quota: `500`

Receiver flux, hit count, contribution과 reflection summary는 path 시각화 저장
여부와 독립적이며, 기존 quota와 Receiver-path 우선 교체 정책을 유지한다.

## Stop과 fallback

Stop의 원자 단위는 primary chunk다. Chunk의 depth query 도중 Stop이 들어와도
남은 active depth와 ordered commit을 완료한 뒤 다음 chunk를 시작하지 않는다.
따라서 partial Receiver grid, contribution 또는 stored path를 노출하지 않는다.
이 계약의 대가로 최악 Stop 지연은 chunk의 남은 전체 bounce 처리 시간이다.

각 active depth batch는 하나의 logical provider query다. Native provider가
initialize, execute 또는 result validation에서 실패하면 해당 depth batch
전체를 Python CPU로 다시 실행한다. 이전 depth의 native 성공은 유지할 수 있어
effective provider가 `mixed`가 될 수 있지만, 실패한 batch 내부에서 native와
CPU row를 섞지 않는다. 첫 hard failure 뒤에는 circuit breaker를 열고 logical
intersection ray/batch 수에는 retry를 중복 반영하지 않는다.

## 성능 metric

기존 provider/intersection metric에 다음 항목을 추가했다.

- 실행 여부와 규모: `intersection_batch_size`,
  `multi_bounce_wavefront_used`, `wavefront_chunk_count`,
  `wavefront_primary_ray_count`
- Depth/compaction: `wavefront_depth_batch_count`,
  `wavefront_max_active_ray_count`, `wavefront_max_observed_depth`,
  `wavefront_active_ray_count_by_depth`, `wavefront_batch_count_by_depth`,
  `wavefront_compacted_ray_count`
- RNG: `wavefront_reflection_rng`, `wavefront_rng_scalar_parity`,
  `wavefront_stochastic_primary_ray_count`
- 시간: `wavefront_state_build_sec`, `wavefront_receiver_sec`,
  `wavefront_plan_sec`, `wavefront_commit_sec`, `wavefront_total_sec`
- Path: `wavefront_path_materialized_count`,
  `wavefront_path_materialization_skipped_count`

결과 전체는 `json.dumps(result.to_dict(), allow_nan=False)`로 직렬화할 수 있다.

## 최종 benchmark

조건:

- 실제 활성 ROI triangle: `45,167`
- primary ray: `100,000`
- `max_depth=10`
- stored path quota: `500`
- 같은 project/geometry/seed

| 실행 | 시간 | Python scalar 대비 | Native scalar 대비 |
| --- | ---: | ---: | ---: |
| Legacy Python scalar 재측정 | `26.1930초` | `1.00x` | `0.43~0.44x` |
| Explicit Numba native scalar | `11.2558~11.4236초` | `2.29~2.33x` | `1.00x` |
| Numba wavefront batch 4,096 | `7.3109초` | `3.58x` | `1.54~1.56x` |
| Numba wavefront batch 1,024 | `7.0649초` | `3.71x` | `1.59~1.62x` |

1,024 chunk의 3회 실행은 `7.4339/7.0649/7.0612초`, p95 `7.3970초`,
처리량 `14,154.5 primary ray/s`였다. 4,096 chunk 중앙값보다 실행시간이
`3.36%` 짧고 처리량은 `3.48%` 높아 runtime 기본 chunk를 1,024로 조정했다.
같은 wavefront seed의 chunk `256/1,024/4,096`과 반복 결과는 exact했다.

1,024 중앙값의 wall-time 구성은 다음과 같다.

- reflection plan: `3.7290초` (`52.8%`)
- ordered commit: `1.6456초` (`23.3%`)
- state build: `0.5258초` (`7.4%`)
- intersection dispatch: `0.4813초` (`6.8%`, native kernel `0.3888초`)
- Receiver batch: `0.2004초` (`2.8%`)

Stochastic legacy 비교는 seed `42/43/44`, 각 100,000 ray로 수행했다. 3-seed
평균 wavefront/legacy 비율은 Receiver hit `0.9908`(-0.92%), surface hit
`1.0033`(+0.33%), Receiver flux `0.9908`(-0.93%)였다. 각 차이의 작은 표본
95% CI는 모두 0을 포함해 유의한 bias 증거는 없었지만 표본이 세 개뿐이라
unbiased를 확정하거나 `auto` 승격을 정당화하기에도 부족하다. 따라서 명시적
batch만 유지하고 더 많은 seed 또는 1M 통계 검증을 후속 gate로 둔다.

Deterministic depth 2/10 synthetic benchmark 재현 명령은 다음과 같다. 실제
ROI 프로젝트 표는 사용자 `.bitsam` smoke 측정이며 repository fixture로
포함하지 않는다.

```powershell
python scripts/benchmark_perf3b2a_multibounce.py --rays 50000 --depth-ten-rays 10000 --repeats 3 --batch-sizes 1024 4096
```

원시 synthetic 결과는 git-ignored
`outputs/perf3b2a_multibounce/summary.json`에 기록된다.

Warm synthetic 중앙값은 다음과 같다. 두 장면 모두 specular라 legacy scalar와
semantic mismatch는 `0`이다.

| Synthetic scene | Python scalar | Numba wavefront 1,024 | Speedup |
| --- | ---: | ---: | ---: |
| 2 bounce, 50,000 ray | `2.4775초` | `1.9762초` | `1.254x` |
| 10 bounce corridor, 10,000 ray | `1.8504초` | `1.5629초` | `1.184x` |

4-triangle corridor처럼 교차가 싼 장면은 native scalar 호출 경계 비용 때문에
오히려 느릴 수 있다. 이는 scene density와 end-to-end gate 없이 native provider를
`auto`로 선택하지 않는 이유이기도 하다.

## 검증

- Specular depth 2에서 chunk `1/7/64/4,096`과 scalar full semantic exact
- 실제 10-bounce corridor에서 scalar/wavefront exact와 1,100 logical query
- Stochastic mixed/Lambertian/Russian-roulette의 chunk/provider/repeat exact
- Receiver vector boundary와 scalar 후보 판정 exact
- Receiver grids, flux-squared, detailed contribution, reflection summary와
  stored-path quota 보존
- Stop 중 시작한 multi-depth primary chunk 원자 commit
- Mid-depth native failure의 whole-query replay, circuit breaker와 logical count
  비중복
- 기본 `auto`의 scalar 결과 exact 및 native probe/call 0회
- 기본 `auto` 5회 중앙값 `0.779초`, 강제 scalar `0.783초`로 CPU 무회귀
- Strict JSON serialization
- 전체 Python suite `146 passed`, `37 subtests passed`

## 한계와 다음 단계

현재 결과는 실제 multi-bounce에서 native intersection batch를 연결하는 것이
유효함을 보여준다. 그러나 100,000-ray 중앙값은 `7.0649초`이고 백만 ray
선형 환산은 약 `70.7초`다. LightTools보다 빠르거나 백만 ray를 목표 시간 안에
처리한다고 주장할 단계가 아니다.

다음 병목은 reflection planning과 원 순서 grid/contribution/path commit이다.
후속 단계에서는 이 구간의 배열화 또는 compiled aggregation을 검토하고,
동일 compact active-ray buffer와 whole-depth fallback 계약을 CUDA backend에
재사용한다. Default `auto` 승격은 최종 실제 ROI end-to-end, stochastic
statistical parity, 기본 CPU 회귀, cold start와 배포 크기를 모두 확인한 뒤
결정한다.
