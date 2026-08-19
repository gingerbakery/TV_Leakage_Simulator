# PERF-3B-2C-2 Compiled Ordered Reducer

## 요약

PERF-3B-2C-2는 `ordered_primary_event_tape_v2`의 summary 계산을 primary 순서
그대로 처리하는 optional Numba CPU reducer를 추가했다. Runtime-only
`wavefront_reducer`는 `auto`, `python_cpu`, `numba_cpu`를 허용한다.

- `auto`: 기존 `python_ordered_v1`을 사용하고 Numba를 import/probe하지 않는다.
- `python_cpu`: Python ordered reducer를 명시한다.
- `numba_cpu`: `soa_event_tape`, `max_depth >= 2`, summary contribution에서만
  `ordered_summary_reducer_v1`을 명시적으로 시도한다.

Detailed contribution은 정상 지원 범위 밖이므로 explicit `numba_cpu` 요청에서도
`detailed_contributions_unsupported` 사유로 Python을 선택한다. 이는 provider
실패 fallback이 아니며 native attempt/fallback count는 모두 `0`이다. Scalar,
single-bounce, `object_reference`, face/polygon legacy 경로도 reducer를 probe하지
않는다.

실제 ROI warm canonical에서 native reducer는 Python reducer 대비 p50 wall을
`5.094436초 -> 4.643004초`, `1.097228x` 개선했다. Warm 성능 gate `>= 1.05x`는
통과했지만 reducer cold JIT `2.382357초`, optional Numba/llvmlite 배포와 기본 CPU
무회귀 원칙 때문에 `auto`는 계속 Python/no-probe다. Native는 명시적 opt-in이다.

## Ordered summary 계약

`ordered_summary_reducer_v1`은 tape의 primary slot을 오름차순으로, 각 primary의
surface event를 CSR 순서로 처리한다. Kernel은 serial strict `float64`,
`fastmath=False`이며 기존 Python reducer의 덧셈 순서를 유지한다. CPython scalar
제곱과 ULP가 달라질 수 있는 Receiver power square는 consumer가 기존 연산으로
미리 계산해 read-only input으로 넘긴다.

Native input은 다음 두 부분으로 나뉜다.

- `OrderedSummaryBatch`: tape core, terminal, face/profile, Receiver/grid binding을
  가진 owned/C-contiguous/read-only 배열
- `OrderedSummaryAccumulator`: 현재 run의 grid, optical/reflection/contribution
  summary를 compact numeric 배열로 옮긴 owned/C-contiguous mutable scratch

Provider는 caller accumulator를 직접 변경하지 않고 복사본에서 kernel을 실행한다.
Result는 전부 owned/C-contiguous/read-only 배열이며 input/result field 사이 storage
alias를 허용하지 않는다. Count headroom, dtype/shape, finite 값, touch order,
float shadow/reference, result digest와 consumer-side 재검증을 통과해야 한다.

Native 범위는 summary numeric reduction이다. 다음은 정확한 기존 의미를 위해
Python stage에 남긴다.

- profile/depth/Receiver dict의 최초 삽입 순서 복원
- public dataclass와 Receiver grid list 생성
- stored-path quota 판정, oldest dead-end 교체와 선택된 path materialization
- detailed face/component/material contribution

Terminal-only tape도 event가 `0`인 정상 input이다. Primary/tape count와 surface
event count를 별도 metric으로 기록해 direct Receiver primary가 native 실행에서
누락되지 않도록 했다.

## Transaction과 fallback

Native 결과는 public summary에 즉시 쓰지 않는다.

1. 현재 public 상태에서 scratch accumulator를 만든다.
2. Provider가 복사본에서 전체 tape를 계산한다.
3. Provider와 consumer가 immutable result/digest/count를 검증한다.
4. Dict, grid, path를 별도 staged commit으로 복원한다.
5. 모든 단계가 성공한 뒤 한 번에 publish한다.

Unavailable, `initialize`, `execute`, `result_validation` 또는 apply 준비 실패 시
public 상태는 바뀌지 않은 채 같은 tape 전체를 `python_ordered_v1`으로 정확히 한
번 replay한다. 그 뒤 run-local circuit breaker를 열어 다음 tape에서 native를
재시도하지 않는다. Logical count에는 native attempt와 Python replay를 중복
더하지 않는다. Unavailable은 capability 선택이므로 hard-fallback count 대신
`wavefront_reducer_unavailable_reason`에 기록한다.

Stop 원자 단위는 기존 primary chunk다. 시작한 chunk의 tape/reducer publish를
완료한 뒤 다음 chunk 경계에서 정지한다. Reducer accumulator와 circuit state는
run-local이며 동시 실행 간 공유하지 않는다. Global lock은 capability/kernel JIT
초기화에만 사용한다.

## Metrics

Performance summary는 requested/effective reducer와 다음 증거를 기록한다.

- selection reason, native available/used/version/disabled
- logical/Python/native-attempt/native-success/fallback의 tape, primary, event count
- fallback phase/reason과 unavailable reason
- native prepare, dispatch, JIT, execute, result-validation, apply와 path time

`wavefront_reducer_native_timing_scope`은
`prepare_external_plus_dispatch_including_jit_wait_and_validation`이다. Timing은
nested 구간이다. 예를 들어 dispatch 안에 kernel execute와 result validation이,
commit 안에 preflight/replay/path stage가 포함되므로 합산하면 안 된다.

## Exact regression

Wall-time threshold 없이 다음 의미 계약을 추가했다.

- deterministic depth 2, depth 10, 여러 chunk와 multi-emitter
- mixed/Gaussian/Russian-roulette stochastic
- object-reference, SoA Python, SoA native의 ordered JSON, 모든 `float64` bit와
  dict insertion order exact
- summary native와 detailed 정상 Python 선택
- terminal-only primary/event dual count
- unavailable/initialize/execute/result-validation whole-tape fallback, circuit와
  no-double-count
- provider input 불변, output owned/read-only/no-alias와 corruption rejection
- stored-path quota의 oldest dead-end Receiver 교체
- Stop primary-chunk atomicity, default no-import/no-probe
- 동시 native run의 accumulator/metric 격리와 strict JSON

최종 전체 suite는 `193 passed, 180 subtests passed`다. 별도 final audit도 input
불변, output ownership, malformed/corrupt result rejection, terminal-only,
auto-no-probe와 execute fallback circuit을 재검증했고 실패 `0`이었다.

## Actual ROI canonical

조건은 PERF-3B-2C-1과 같은 사용자 ROI workload다.

- 원본/활성 ROI triangle `50,944 / 45,167`
- primary `100,000`, `max_depth=10`, seed `42`
- summary, stored paths `500`, chunk `1,024`
- explicit Numba intersection, planner `auto`, pipeline `soa_event_tape`
- reducer별 10k warmup 뒤 3회, measured 순서 `P,N,N,P,P,N`
  (`P=python_cpu`, `N=numba_cpu`)
- 측정 전후 source hash 동일

| Reducer | Wall p50 | Wall p95 | Primary ray/s p50 | 1M 선형 환산 |
| --- | ---: | ---: | ---: | ---: |
| `python_cpu` | `5.094436초` | `5.128807초` | `19,629.26` | `50.94초` |
| `numba_cpu` | `4.643004초` | `4.697531초` | `21,537.78` | `46.43초` |

Native는 p50 `1.097228x`, wall `8.861%`, p95 `1.091809x`, wall `8.409%`
개선했다. 이는 같은 SoA tape의 reducer A/B이며 object-reference 또는 LightTools와
직접 비교한 수치가 아니다.

| P50 nested timing | Python | Native |
| --- | ---: | ---: |
| Plan 전체 | `2.959407초` | `2.962245초` |
| Commit 전체 | `1.101820초` | `0.643344초` |
| Reducer replay | `1.062883초` | `0.443459초` |
| Native prepare | `0초` | `0.174618초` |
| Native dispatch | `0초` | `0.310282초` |
| Native kernel execute | `0초` | `0.021806초` |
| Native result validation | `0초` | `0.237018초` |
| Native apply | `0초` | `0.133177초` |
| Native path stage | `0초` | `0.023353초` |

Reducer replay 자체는 `2.3968x`, commit 전체는 `1.7126x` 빨라졌다. Plan은
`0.096%` 차이로 같은 범위였다. Kernel은 `0.0218초`뿐이고 prepare,
result-validation과 Python object apply가 현재 native reducer의 더 큰 비용이다.

모든 warmup/measured run은 seven semantic/hash family, Receiver grid,
contribution, path와 ordered float bits가 exact했다.

- Receiver/surface/terminated: `12,652 / 225,482 / 87,348`
- Receiver flux: `0.040176617410112817`
- Stored path: `500`, materialized/skipped `931 / 99,069`
- Tape/primary/event: `98 / 100,000 / 225,482`
- Tape peak/copy: `680,048 / 29,407,112 bytes`
- Native attempt/success: `98 / 98`, fallback `0`

Intersection은 effective `numba_cpu`, attempt/success `1,078 / 1,078`, row
`309,119`, fallback `0`이었다. Planner `auto`는 effective `python_cpu`,
logical/Python-sidecar `225,482 / 225,482`, native attempt/fallback `0`이었다.
별도 paths-off 10k quick는 `1.081299x`, exact를 기록했다. Detailed synthetic은
explicit native 요청에서도 정상 Python 선택, attempt/fallback `0`, exact였다.

Sampled process RSS peak delta p50은 Python `4,210,688 bytes`, native
`3,735,552 bytes`였지만 allocator 상태에 민감한 소표본이므로 native memory
우위로 해석하지 않는다. Tape peak/copy는 두 reducer가 동일하다.

Cold warmup에서 reducer JIT compile은 `2.382357초`였다. 장시간 반복 실행에서는
warm 이득이 있지만 짧은 단발 실행, Numba가 없는 PC와 lightweight package에는
손익이 다르다. 따라서 `wavefront_reducer="auto"`는 Python/no-probe를 유지한다.

## Artifact와 다음 단계

Actual artifact는 git-ignored
`outputs/perf3b2c2_compiled_reducer/actual_roi_summary.json`이며 SHA256은
`04bb4514a3a5909a5f8afbc551cecd4de3c84b70c11cada6d9335f7ec5dcf648`다.
Final audit SHA256은
`feacdb1acbb7e757d4690147bea8bf0e9a6b75439cc81b4573faa43e1877846a`다.
실제 사용자 `.bitsam`/CAD는 hash만 기록하고 repository fixture로 추가하지
않았다.

Actual 전용 git-ignored harness SHA256은
`f11985a62911d0eb47312adb46990dd8c0bee6c11dc503c78667748ba49123e8`, repository
benchmark script SHA256은
`0c7308f1a7effffde4b3efb534181d4dfbd369247ae5625731eb2acc7e688834`다.

Repository의 `benchmark_perf3b2c2_ordered_reducer.py`는 committed deterministic
depth-10 scene에서 Python/native reducer를 counterbalanced 실행하고 provider
증거, ordered JSON/float-bit hash, RSS/tape memory와 source 전후 hash를 기록한다.
`--no-write`로 artifact를 만들지 않는 smoke가 가능하다.

후속 우선순위는 다음과 같다.

1. prepare/result-validation/Python apply 비용 축소
2. stochastic planning 범위를 넓히는 `counter_rng_v2`
3. 같은 SoA/tape와 whole-batch fallback을 재사용하는 CUDA backend

PERF-3B-2C-2는 CPU summary reducer 가속 단계다. 백만 ray 선형 환산
`46.43초`는 이전보다 개선됐지만 LightTools 이상의 최종 GPU 목표 달성을
의미하지 않는다.
