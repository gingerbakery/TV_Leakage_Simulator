# PERF 5C GPU 커널 후보와 재검산 병목 검증

후속 queue 분리 실행 구현과 성능 비교는
[`GPU 재검산 분리 실행`](2026-10-19_perf5c-compacted-replay.md)을 따른다.
아래의 다음 구현 방향은 당시 계획이며, 후속에서 구현했으나 가속 기준은
통과하지 못해 기본 경로를 유지했다.

**CPU와 GPU 각각 2배 목표는 아직 전체 완료가 아니다.** 이번에는 GPU 커널의
추가 가속 후보와, 빠른 BVH에서 기존 면 판정을 보존하는 재검산 방식을 실제
장치에서 비교했다. 후보들은 기본 프로그램에 적용하지 않았다. 현재 기본
소스의 성능 상태는 [직전 고정 기준 보고](2026-10-19_perf5c-uniform-grid-and-locality.md)의
CPU 자동 7조건 2.111~4.882배, GPU 복잡 CAD 1.309배와 같다.

새로 확인한 핵심은 재검산 광선의 비율보다 실행 그룹의 재검산 진입 비율이
훨씬 높다는 점이다. 19.47%의 교차 질의만 기존 BVH를 다시 계산해도, 거의
모든 실행 그룹이 그 분기를 실행했다. 다음 개선은 광선 전체의 worker 교체가
아니라 **재검산 질의를 별도 GPU queue로 압축하는 방식**에 집중한다.

## 커널과 연산 후보

각 행은 해당 세션에서 현재 GPU 경로와 교대 실행한 warm 중앙값이다. 서로
다른 세션의 비율을 곱하지 않았으며, CAD import와 BVH 사전 준비는 별도다.

| 조건과 후보 | 현재 경로 | 후보 | 속도비 | warm 횟수 |
| --- | ---: | ---: | ---: | ---: |
| CAD 100K 설정별 분기 고정 | 0.176222초 | 0.169497초 | 1.040배 | 5 |
| CAD 100K register 64 제한 | 0.176222초 | 0.174602초 | 1.009배 | 5 |
| CAD 100K 동일 광학 값 고정 | 0.176222초 | 0.173806초 | 1.014배 | 5 |
| CAD 100K 단위 벡터 정규화 생략 | 0.165692초 | 0.167727초 | 0.988배 | 7 |
| CAD 100K FP64 sin과 cos 공동 계산 | 0.165692초 | 0.165396초 | 1.002배 | 7 |
| CAD 100K FP64 연산 후보 통합 | 0.165692초 | 0.166204초 | 0.997배 | 7 |
| CAD 100K 원래 순서 2삼각형 경계 묶음 | 0.177086초 | 0.188545초 | 0.939배 | 5 |
| CAD 100K 원래 순서 4삼각형 경계 묶음 | 0.177086초 | 0.189888초 | 0.933배 | 5 |
| 확률 반사 1M persistent worker 8,192 | 0.152893초 | 0.156144초 | 0.979배 | 5 |

Register 제한은 보관 공간을 다른 곳으로 옮기는 비용도 만들었다. 기준은
thread당 register 114개와 local memory 40 bytes이고, 64개 제한은 local
memory 352 bytes, 80개 제한은 192 bytes였다. Register 수 감소를 곧바로
속도 향상으로 해석하지 않는다.

설정 고정의 약 4% 이득은 CAD 한 조건의 결과다. 설정마다 추가 JIT가 생기는
비용과 다른 조건의 채택 기준까지 통과한 것은 아니므로 기본 적용하지 않았다.
광학 값 고정은 모든 값의 FP64 비트가 같은 열에만 적용했다. `-0.0`과 `+0.0`도
같은 cache key로 합치지 않는다. FP32로 정밀도를 낮춘 후보는 없다.

CAD 대조군은 100,000 primary 중 약 99,997개가 깊이 10까지 남는다. 이 조건에서는
중간에 끝난 primary worker를 새 광선으로 채우는 방식이 큰 이득을 주지 않았다.
확률 반사에서도 queue 준비 비용을 포함한 전체 시간은 개선되지 않았다.

## 빠른 BVH와 정확도 보호

기존 BVH의 최대 leaf 크기 8 대신 leaf 1 구조를 별도로 준비했다. 원래 triangle
좌표와 면 번호, 기존 BVH는 보관한다. 새 구조에서 가까운 후보끼리 `1e-10 mm`
이내이거나 경계 판정이 불확실하면, 같은 광선을 GPU의 기존 BVH로 다시 계산한다.
큰 좌표와 심하게 퇴화한 geometry는 원래 구조를 그대로 쓴다.

별도 후보에서는 새 BVH의 경계만 바깥으로 넓혀 불필요한 동적 경계 검사 비용을
줄였다. 실제 triangle, 광학 값과 반사 횟수는 바꾸지 않았다. 이 역시 검증용
후보이며 모든 CAD에 대한 일반적인 정확성 보장을 뜻하지 않는다.

CAD 100,000 Ray, 같은 세션 warm 7회다.

| 후보 | 중앙값 | 현재 경로 대비 | 결정 |
| --- | ---: | ---: | --- |
| 현재 GPU | 0.174536초 | 1.000배 | 기본 유지 |
| 빠른 leaf 1만 사용 | 0.120074초 | 1.454배 | 면 속성 오류로 채택 불가 |
| 빠른 BVH와 GPU 기존 탐색 재검산 | 0.208380초 | 0.838배 | 속도 기준 실패 |
| 외향 경계 여유와 재검산 | 0.185332초 | 0.942배 | 속도 기준 실패 |
| 외향 경계와 기존 함수 통합 | 0.184893초 | 0.944배 | 속도 기준 실패 |

추가 두 BVH의 CPU 준비 합계는 3.057초였다. Kernel을 새로 준비하는 첫 호출은
기준 5.577초, 재검산 후보 약 4.6~4.8초로 따로 기록했다. 이 값들을 warm 속도와
혼동하지 않는다. 추가 BVH는 node 저장량과 VRAM도 늘린다.

### 단순 CAD 비교를 통과해도 채택할 수 없는 이유

검증용으로 두 면을 `5e-11 mm` 간격으로 배치했다. 원래 판정이 고르는 면에는
반사율 80%의 경면, 더 가까운 다른 면에는 흡광 속성을 넣었다. 재검산 없는
leaf 1 후보는 다른 면을 골라 Receiver 도달 Ray가 0이 됐다. 반면 원래 경로와
재검산 후보는 수광 결과가 같았다.

즉, 단순 CAD의 aggregate 비교가 통과해도 실제로 다른 면 속성을 계산할 수
있다. 빠른 후보를 기본에 넣지 않았으며, 이를 의도적으로 검출하는 negative
control을 테스트에 남겼다. 전체 테스트의 통과는 이 잘못된 후보가 정확하다는
뜻이 아니라, **그 후보의 오류를 검출하는 검사도 통과했다는 뜻**이다.

![GPU 추가 후보 비교](../../outputs/perf5_transport/kernel-trials-comparison.png)

## 재검산 분기 계측

외향 경계와 함수 통합 후보에 GPU 정수 counter를 넣은 별도 실행이다.
시간 계측을 위한 benchmark가 아니며, 계측 실행의 cold 시간 6.599초를 위
속도 표와 비교하지 않는다.

| 항목 | 관측값 |
| --- | ---: |
| Primary Ray | 100,000 |
| 논리 교차 질의 | 1,099,987 |
| 기존 BVH 재검산 질의 | 214,170 |
| 질의 기준 재검산 비율 | 19.470% |
| 교차 함수 진입 subgroup 실행 | 34,375 |
| 재검산 subgroup 실행 | 34,337 |
| 두 subgroup 실행 수의 비율 | 99.889% |
| 교차 진입 subgroup 평균 활성 lane | 31.9996 |
| 재검산 subgroup 평균 활성 lane | 6.2373 |

한 그룹은 최대 32 lane이 함께 실행한다. 이번에는 재검산 함수에 진입한 그룹
안에서 평균 약 6.24 lane만 해당 작업을 수행했다. 이는 **해당 분기 내부의
활성 lane 수**이며, GPU 전체 사용률이 20%라는 뜻은 아니다. 단순히 재검산
질의 수만 20%로 줄여도 그 비용이 20%로 줄지 않는 이유를 보여준다.

다음 실험은 이 질의들을 연속된 GPU queue로 모아 전용 kernel에서 처리한다.
현재의 결과는 이 방향을 뒷받침하지만, 실제 가속비는 구현 후 전체 시간으로
다시 확인해야 한다.

## 정확도와 안전성 검사

- 통합 318검사 통과: 301.564초. 기존 수송·물성·종료·Aim·저장 관련 검사와
  이번 후보의 별도 검사를 포함한다.
- 동적 경계/외향 경계 두 방식의 scalar oracle 6,000질의에서 면 번호와 거리
  비트가 원래 판정과 같았다. 미세 간격, 경계, miss, 거의 평행한 방향,
  ignore face와 최대 거리를 포함한다.
- 실제 GPU에서 미세 간격에 따른 면 속성 오류를 재현하고, 재검산 보호가
  원래 결과를 보존함을 확인했다. CAD의 경로와 광학 값 재편집 결과도 비교했다.
- Persistent worker는 부분 tail과 Receiver MIS의 원래 RNG/출력 행/경로를
  보존했다. 실제 1,000회 반사도 검사했다.
- 외향 경계 BVH도 실제 1,000회 반사의 17 × 1,001 surface hit와 경로를 보존했다.
- 실제 장치 memcheck 3검사, 37.529초, 오류 0건이다. synccheck 2검사,
  24.151초, 오류 0건이다.
- 실험 초기 2삼각형 묶음에서 CUDA `min` 호출이 compile되지 않아 실행 증거
  gate가 중단된 로그도 보관했다. 명시적인 분기로 고친 뒤에만 최종 비교했다.
  CPU 대체 실행 시간을 GPU 성능으로 사용하지 않았다.

기본 atomic 집계의 미세한 비결정성과 LT/실측 정합을 이번 검사로 해결했다고
주장하지 않는다. 기존 정밀 집계 40조건의 검증 범위는 직전 보고를 따른다.

## 장치와 실행 증거

Git source checkout `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`의 로컬 작업본,
Python 3.13 x64, Numba 0.66.0, NVIDIA GeForce RTX 3070, driver 591.59,
CUDA Toolkit 13.1 환경이다. 각 최종 결과는 source hash와 script hash를 기록했다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

이 preflight는 production Ray/BVH를 검사한다. 후보 자체의 검증은 위 실제
실행·대조 검사와 별도다. 정상 후보 비교는 `compute_backend=gpu_cuda`, BVH,
`compute_execution_state=gpu_active`, CPU hybrid/resident fallback 0이다.
CAD 100K 비교는 CUDA 성공/시도 2/2, 확률 반사 1M은 16/16이다. Source는
Datum Plane emitter다. 재검산은 GPU 안에서 수행되며 CPU fallback이 아니다.
첫 호출과 각 warm raw는 원본 JSON에 남겼다.

## 다음 구현 방향

PERF 5C의 다음 후보는 GPU 안에서 한 반사 단계씩 처리하는 분리 실행이다.

1. 현재 active Ray에 빠른 BVH를 적용하고 불확실한 질의만 GPU queue로 압축한다.
2. 압축된 질의에 원래 BVH를 적용한다. 모든 primary가 masked 상태로 재검산
   분기를 실행하지 않도록 별도 kernel을 쓴다.
3. 확정된 교차 결과로 기존 FP64 반사 계산을 수행하고 survivor queue를 만든다.
4. 마지막 결과 집계와 표시 path 복원은 기존 계약을 유지한다.

구현에서 지켜야 할 조건은 다음과 같다.

- 원래 primary 번호로 event와 terminal을 기록한다. Queue 순서를 RNG나
  summary 합산 순서로 사용하지 않는다. Source packet과 MIS weight도 유지한다.
- Receiver까지의 최대 교차 거리를 기존 FP64 scan과 똑같이 계산한다.
  무조건 무한대로 query한 뒤 잘라내면 Receiver 부근의 동률 면 선택이 달라질 수 있다.
- Source face ignore와 ROI 원본 face/광학 override를 그대로 유지한다.
- Host의 초기 batch를 보존한다. GPU 입력 사본을 state로 재사용할 경우에도
  오류 복구와 sparse path 재추적 전에는 초기 값을 다시 올린다.
- Active/재검산 queue는 정수 counter와 warp 단위 예약으로 만든다. Depth마다
  광선을 CPU에 내려받아 정렬하지 않는다.
- Bounded recording window와 commit 전 재실행을 유지한다. 기록 한도에
  도달한 뒤 1,000개 빈 kernel을 실행하지 않도록 launch 범위도 제한한다.
- 중간 kernel 오류와 Stop에서 기존 prefix를 중복 반영하지 않는다.
- 작은/단순 장면에는 준비·launch 비용이 증가할 수 있으므로 기존 경로와
  대표 조건의 3% 회귀 gate를 함께 확인한다.

이 분리 실행은 아직 구현하지 않았다. 2배 목표나 PERF 5C 전체 완료로
판정하지 않으며, 이번 실험으로 정상 앱을 더 느린 후보로 교체하지 않았다.
UI, 환경 설치, 서버 재시작, 커밋과 push도 변경하지 않았다.

## 원본 자료

- `outputs/perf5_transport/kernel-specialization-cad-probe.json`
- `outputs/perf5_transport/triangle-groups-cad-final.json`
- `outputs/perf5_transport/persistent-workers-stochastic-probe.json`
- `outputs/perf5_transport/fp64-math-cad-probe.json`
- `outputs/perf5_transport/guarded-bvh-padded-final.json`
- `outputs/perf5_transport/guarded-query-subgroups.json`
- `outputs/perf5_transport/kernel-trials-full-regression.log`
- `outputs/perf5_transport/kernel-trials-memcheck.log`
- `outputs/perf5_transport/kernel-trials-synccheck.log`
- `outputs/perf5_transport/triangle-pair-diagnostic.log`: 초기 실험 compile 실패.
