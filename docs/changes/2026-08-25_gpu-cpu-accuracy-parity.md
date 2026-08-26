# GPU/CPU 정확도 정합 및 Receiver 표본 품질 개선

## 문제

동일 장면에서 GPU 결과가 CPU 대비 다음처럼 달라진다는 보고가 있었다.

- Peak-area Error 약 60% 증가
- Error Estimate 약 65% 증가 및 수렴 실패
- Peak/Mean nit, Flux 50~80% 감소
- Receiver Heatmap 노이즈 증가
- 약 1,200만 Ray 실행 시 매우 긴 계산 시간

## 진단 결론

주원인은 CUDA BVH의 부동소수점 계산 오류가 아니라 CPU와 GPU가 서로 다른
Monte Carlo 실험을 수행한 것이었다.

- CPU 기본 경로: legacy scalar sampler와 depth-first Python RNG
- GPU 기본 경로: vectorized batch sampler, SoA event tape, `counter_rng_v2`
- Face emitter도 CPU scalar와 GPU batch의 primary sample이 달랐다.

빛샘처럼 Receiver hit 확률이 매우 낮은 장면에서는 10만 Ray 중 hit가 1~2개일 수
있다. 서로 다른 난수 stream에서 hit가 `2 대 1`이면 Flux가 약 50% 차이 나는 것이
가능하며, 이것은 장치 산술 오차와 구분해야 한다.

CUDA BVH 자체는 actual STEP 50,944 triangle에서 200,000 ray를 비교한 결과 face
mismatch `0`, distance tolerance mismatch `0`, 최대 절대 거리 오차
`2.842170943040401e-14`였다.

## 수정 내용

### CPU/GPU 동일 Monte Carlo 계약

프로덕션 full-auto 실행은 CPU와 GPU 모두 다음 계약을 사용한다.

- `monte_carlo_contract=cpu_gpu_deterministic_batch_v1`
- 동일 vectorized primary sampling
- 동일 `counter_rng_v2` semantic-lane 난수
- 동일 SoA event tape와 ordered reducer
- 동일 run accumulator commit 순서
- 교차 장치만 CPU `numba_cpu` 또는 GPU `gpu_cuda`로 변경

Face emitter도 CPU/GPU 모두 batch 생성하며 source face를 row별로 보존한다.
개발자가 runtime 인자를 직접 지정하는 legacy 진단 경로는 계속 사용할 수 있지만
UI/.bitsam의 일반 실행에는 적용되지 않는다.

### 오차와 Heatmap 품질 분리

Receiver별로 다음 metric을 추가했다.

- `statistical_quality`
- `receiver_hit_rate`
- `estimated_rays_for_minimum_hits`
- `heatmap_quality`
- `heatmap_hits_per_bin`
- `estimated_rays_for_usable_heatmap`

zero-hit 결과가 과거처럼 Error `0%`로 보이지 않도록 `100%`와 `no_hits`로
표시한다. Heatmap은 평균 `5 hit/cell`을 1차 usable 기준으로 사용한다. 전체 Flux
오차가 낮더라도 셀별 hit가 부족하면 UI에서 `Heatmap · Sparse/Noisy`로 경고한다.

### 결과 UI의 실행 증거

현재 계약으로 생성한 결과에는 `CPU/GPU 동일 샘플 계약` 배지를 표시한다. GPU가
실행됐지만 이 계약이 없는 이전 결과에는 현재 버전으로 다시 해석하라는 경고를
표시한다.

### 독립 검증 명령

```powershell
.\.venv-gpu\Scripts\python.exe scripts\verify_gpu_cpu_accuracy.py --rays 100000
```

검증기는 production CUDA preflight, Face direct, stochastic two-bounce를 실행하고
CPU/GPU 전체 semantic payload exact, GPU batch success, 계약 ID를 모두 확인한다.

## RTX 3070 검증 결과

### Production preflight

- GPU: NVIDIA GeForce RTX 3070, compute capability 8.6
- Numba: 0.66.0
- strict FP64: true
- kernel executed/verified: true/true
- scope: `production_ray_bvh`
- provider contract: `strict_float64_bvh_v1`

### 100,000 Ray 정확도 gate

| Case | CPU/GPU semantic | Receiver hit | Peak nit | Mean nit | Flux | GPU 상태 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Face direct | exact | 71,635 | 95.843985856 | 23.879442564 | 0.562646110 lm | gpu_active |
| Stochastic 2-bounce | exact | 1 | 1711.083934632 | 11.882527324 | 0.004977341 lm | gpu_mixed |

Stochastic case는 CPU/GPU가 exact지만 hit가 1개라 Error와 Peak-area Error가 모두
`100%`, 품질은 `insufficient_hits`다. 즉 장치 정합 통과와 결과 수렴은 별도다.

### Actual STEP 성능/정합

- 200,000 intersection: GPU `7.329M ray/s`, Numba CPU 대비 `6.329x`
- 400,000 primary, depth 10 end-to-end:
  - CPU `9.3921초`
  - GPU `5.0452초`
  - speedup `1.8616x`
  - 4,400,000 intersection row
  - count/grid/summary exact, path mismatch `0`, hard fallback `0`

별도 실제 TV ROI 저장 조건 100,000 Ray 검증에서도 CPU/GPU hit `9,086`, Peak
`14.033001130`, Mean `0.988762967`, Flux `0.023297179 lm`, Error
`1.641346716%`, Peak-area Error `1.704736095%`가 exact였고, CPU `7.772초`, GPU
`1.501초`로 약 `5.18x`였다.

## 1,200만 Ray가 오래 걸린 이유

- 이전 Face emitter는 일부 primary를 CPU scalar로 처리해 GPU를 충분히 사용하지
  못했다. 현재 Face batch/CUDA BVH 연결로 수정했다.
- `Auto convergence`는 기존 결과에 Ray를 추가하는 방식이 아니라 각 배수를 처음부터
  다시 실행한다. `1→2→4→8배`는 마지막 8배만 계산하는 것이 아니라 누적 `15배`
  Ray를 처리한다.
- 장면 triangle 수, 반사 depth와 생존 ray 수가 커지면 실제 intersection 수는
  `primary ray × (직접 + 반사 wave)`로 증가한다.
- Receiver hit가 극히 희박하면 단순 Ray 증가는 통계적으로 비효율적이다. 향후
  Importance Sampling/Next Event Estimation이 필요한 이유다.

400,000 primary depth-10 실측을 단순 선형 환산하면 같은 synthetic 조건의 1,200만
Ray GPU는 약 151초지만, 실제 대형 TV CAD와 자동 수렴의 반복 실행은 훨씬 길 수
있다. 이 환산값을 모든 장면의 보장 시간으로 사용하지 않는다.

## 신뢰 범위와 남은 과제

- 이번 수정은 CPU/GPU **수치 구현 정합성**을 확보한다.
- 소재 반사율, 산란 모델, 광원 power와 `k_abs`가 실제 제품을 얼마나 잘 나타내는지는
  실측 보정이 별도로 필요하다.
- Heatmap이 `Sparse/Noisy`면 Peak pixel과 미세 패턴은 신뢰하지 않는다.
- 사내 실제 TV CAD와 `.bitsam`을 고정 regression fixture로 등록해 매 release마다
  CPU/GPU parity gate를 실행해야 한다.
- 대규모 희귀-event 속도 개선의 다음 우선순위는 Importance Sampling/Next Event
  Estimation과 Auto convergence의 누적 sample 재사용이다.

## 검증

- Python backend: `296 tests` 통과
- GPU accuracy gate: `passed=true`
- Frontend: `26 files / 155 tests` 통과
- Frontend typecheck, lint, production build 통과
- Python `compileall` 통과
