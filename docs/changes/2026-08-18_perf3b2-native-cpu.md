# PERF-3B-2 Optional Native CPU Provider

## 결과

PERF-3B의 `RayBatch`/`RayHitBatch` 계약 뒤에 strict-float64 Numba BVH
provider를 연결했다. 실제 50,944-triangle CAD의 100,000-ray intersection
micro의 canonical run은 Python scalar BVH보다 최대 `50.45x` 빨랐고 face
index와 distance bit mismatch는 모두 0이었다. 독립 재실행은 `48.98x`로 같은
결론을 보였다.

그러나 100,000-ray 단일 반사 synthetic end-to-end에서는 native batch가 기존
Python scalar의 `1.009x`로 baseline 수준이었다. 독립 재실행은 `0.961x`였다.
교차가 싼 장면에서는
Receiver 판정, reflection planning, hit materialization과 row-order commit이
지배적이기 때문이다. 자동
선택 승격 기준 `1.20x`를 충족하지 못했으므로 이번 provider는 명시적
개발/benchmark opt-in으로 유지한다.

일반 사용자 기본 `intersection_provider="auto"`는 Numba를 import하거나
capability probe하지 않는다. 따라서 GPU 또는 optional dependency가 없는 PC는
기존 Python CPU 경로를 그대로 사용한다.

## 구현 범위

### Immutable native scene

기존 prepared triangle과 flat BVH에서 다음 연속 배열을 한 번 생성한다.

- triangle `v0`, `edge1`, `edge2`: `float64`
- BVH node bounds: `float64`
- child/start/count와 ordered original face id: `int64`

배열은 read-only이며 동일 geometry run에서 재사용한다. vertex 또는 face가
추가되면 기존 prepared/BVH cache와 함께 무효화된다. Provider 선택은 run별
context에 보관하고 공유 mesh의 configured backend를 변경하지 않는다.

### Lazy optional provider

`native_cpu_intersection.py`는 module import 시 Numba를 import하지 않는다.
명시적으로 `numba_cpu` provider가 실제 호출된 경우에만 capability를 확인하고
kernel을 만들고 JIT compile한다. Kernel은 serial `nogil` 실행이며 API가 여러
job을 동시에 실행할 때 내부 thread pool이 중첩되는 문제를 피했다.

Prototype dependency는 별도 `requirements-acceleration.txt`에 고정했다.
lightweight desktop build는 변경하지 않았다.

### 정합성

Native kernel은 기존 Python BVH와 다음 규칙을 공유한다.

- `float64`, `fastmath=false`
- `t > min_t`, `t <= max_t`
- miss sentinel `(+inf, -1)`
- row별 `ignore_face`
- `trace_excluded` face 제외
- determinant epsilon `1e-8`
- AABB parallel threshold `1e-12`
- 동거리 허용 오차 `1e-10`에서 작은 원본 face index 선택
- 입력 row 순서 보존

큰 CAD 좌표 `1e9 mm`, 경계값, shared edge, duplicate triangle, excluded face,
혼합 hit/miss와 cache invalidation을 별도 테스트했다.

### Atomic fallback와 circuit breaker

Provider 부재는 정상적인 CPU 선택으로 처리하고 failure count를 늘리지 않는다.
초기화, 실행 또는 결과 검증이 실패하면 시작한 logical scalar/batch query 전체를
Python CPU로 다시 실행한다. 일부 native 결과와 fallback 결과를 같은 query에
섞지 않는다.

첫 hard failure 후에는 해당 run에서 native provider를 비활성화해 남은 chunk가
실패를 반복하지 않게 한다. Logical intersection batch/ray count에는 retry를
중복 반영하지 않고 native attempt, success와 fallback을 별도 metric으로
기록한다.

## Benchmark

환경:

- Windows 10, Python 3.13.3
- Numba 0.66.0, llvmlite 0.48.0
- strict serial CPU kernel
- 동일 seed, warm 실행 3회 중앙값

### 실제 CAD intersection micro

CAD: `tv_leakage_roi_right_bottom_no_gap.stp`, 50,944 triangle, 100,000 ray.

| 실행 | 중앙값 | 처리량 | Python scalar 대비 |
| --- | ---: | ---: | ---: |
| Python scalar BVH | 4.4195초 | 22,627 ray/s | 1.00x |
| Native scalar call | 1.0627초 | 94,101 ray/s | 4.16x |
| Native batch 256 | 0.1173초 | 852,575 ray/s | 37.68x |
| Native batch 4,096 | 0.0907초 | 1,102,531 ray/s | 48.73x |
| Native batch 65,536 | 0.0876초 | 1,141,467 ray/s | 50.45x |

- face mismatch: `0`
- distance bit mismatch: `0`
- BVH build: `0.9063초`
- native scene pack: `0.0706초`
- 최초 JIT compile: `1.5052초`
- 최초 native execute: `0.0858초`
- 최초 native cold wall: `1.9288초`

### 단일 반사 end-to-end

RT-2C synthetic, 100,000 primary ray, 200,000 CAD query, stored paths OFF.
Native provider와 같은 조건을 만들기 위해 모든 case에
`intersection_backend="bvh"`를 강제했다. 일반 소형 장면의 `auto`는
brute-force를 선택하므로 이전 PERF-3B-1 scalar wall time과 직접 비교하지 않는다.

| 실행 | 중앙값 | 처리량 | Python scalar 대비 |
| --- | ---: | ---: | ---: |
| Python scalar | 3.1828초 | 31,419 ray/s | 1.000x |
| Native scalar | 4.6694초 | 21,416 ray/s | 0.682x |
| Python reference batch | 4.4098초 | 22,677 ray/s | 0.722x |
| Native batch 4,096 | 3.1531초 | 31,715 ray/s | 1.009x |

모든 case의 receiver hit, surface hit, terminated count와 Receiver flux가 exact
일치했고 semantic mismatch는 0이었다.

별도 `--no-write` 독립 재실행에서는 최대 micro speedup `48.98x`, native batch
end-to-end `0.961x`를 기록했다. 두 실행 모두 실제 교차 kernel은 약 49~50배
가속됐지만 전체 단일 반사 pipeline은 baseline 수준이라는 같은 결론이며,
자동 선택 gate `1.20x`와는 충분히 떨어져 있다.

재현 명령:

```powershell
python scripts/benchmark_perf3b2_native_cpu.py --rays 100000 --e2e-rays 100000 --repeats 3 --batch-sizes 256 4096 65536
```

원시 결과는 git-ignored `outputs/perf3b2_native_cpu/summary.json`에 기록된다.

## 배포 및 기본 CPU 영향

측정된 module directory 크기는 Numba 약 33.2 MiB, llvmlite 약 116.6 MiB로
합계 약 149.8 MiB다. dist-info, 추가 native library와 archive overhead를
포함한 실제 배포 증가는 이보다 조금 클 수 있다. 현재 lightweight package에는
포함하지 않으며, JIT cache와 배포 PC native runtime 호환성을 별도 검증한 뒤
판단한다.

기본 `auto`는 provider 분기만 지나 기존 Python 교차를 사용하고 Numba probe,
scene pack 또는 JIT를 수행하지 않는다. Native provider가 없는 상태, provider
초기화 실패, 실행 실패와 invalid result를 모두 Python reference 결과와 exact
비교했다.

별도 수동 기본 CPU 회귀 검증은 PERF-3B-1 parent와 현재 코드를 동일
프로세스에서 순서 교대해
각각 13회 측정했다.

| 100,000-ray synthetic scalar | 중앙값 | 처리량 |
| --- | ---: | ---: |
| PERF-3B-1 parent | 2.7708초 | 36,091 ray/s |
| 현재 default `auto` | 2.7824초 | 35,940 ray/s |

runtime 중앙값 차이는 `+0.42%`, paired mean 차이는 `+0.03%`로 3% 회귀 gate
안의 측정 잡음 수준이었다. Semantic payload는 exact 일치했다. Fresh process
실행 전후 `numba` import, capability probe, kernel/JIT, scene packing과 native
attempt가 모두 0임을 함께 확인했다.

## 검증

- 실제 Numba kernel과 Python BVH seeded batch bit-exact 비교
- min/max inclusive/exclusive, ignore face와 miss sentinel
- trace-excluded, tie, shared edge, AABB/determinant 경계
- 큰 좌표, cache read-only/invalidation
- scalar/batch end-to-end exact 비교와 strict JSON
- 기본 auto가 native probe를 호출하지 않는 회귀 테스트
- unavailable, initialize, execute와 result-validation fallback
- hard failure circuit breaker와 logical count 중복 방지
- 신규 PERF-3B-2 테스트 15개 통과
- 전체 Python 테스트 138개 통과

## 판단과 다음 단계

Native intersection 자체는 GPU backend로 넘어갈 가치가 충분한 성능을
보였다. 다만 사용자가 문제로 제시한 백만 ray, `max_depth=10` workload는 현재
PERF-3B-1의 단일 반사 wavefront 범위를 벗어난다. 이번 단계만으로 그 실행이
LightTools보다 빠르다고 주장하지 않는다.

명시적 `numba_cpu` scalar provider는 다중 반사 query에도 호출할 수 있지만,
단일 반사 synthetic end-to-end가 `0.682x`였고 기본 `auto`는 이를 선택하지
않는다. 따라서 현재 기본 경로는 사용자의 실제 `max_depth=10` workload를 아직
가속하지 않는다.

다음 단계는 depth별 active ray를 compact하는 multi-bounce wavefront와
Receiver/reflection/grid 후처리 batch다. 그 동일 buffer와 immutable scene을
CUDA에 올려 device buffer 재사용, kernel, download와 fallback을 측정한다.
