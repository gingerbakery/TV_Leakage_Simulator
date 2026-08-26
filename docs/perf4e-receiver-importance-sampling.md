# PERF-4E Receiver 중요도 샘플링·표본 재사용 계약

## 목표

PERF-4E는 같은 Ray를 더 빠르게 계산하는 단계가 아니라, 작은 Receiver에서 목표
오차를 얻는 데 필요한 Ray 수 자체를 줄이는 단계다. 현재 구현은 다음 세 기능으로
구성한다.

1. Emitter primary Ray의 Receiver-directed MIS
2. Lambertian 반사점의 Receiver-directed bounce MIS
3. Auto convergence의 독립 구간 표본 누적 재사용

## Primary Receiver MIS

### 확률 밀도와 가중치

- 원래 광원 방향 분포: `p_source`
- Receiver 면을 균일 샘플링한 방향 분포: `p_receiver`
- Receiver 표본 비율: `α`
- 혼합 분포: `q = (1-α)p_source + αp_receiver`
- Ray power 보정: `w = p_source / q`

따라서 Receiver 방향 Ray를 많이 생성해도 광량 기대값은 원래 광원 분포와 동일하다.
기본 `α=0.5`에서는 유효 weight 상한이 2다.

### 지원 범위

- Emitter: batch 가능한 CAD face·datum plane
- 방향 분포: Lambertian, isotropic
- Receiver: 활성 rectangle Receiver 전체
- CPU/GPU: 동일 weighted primary batch 사용
- Gaussian과 scalar-only emitter는 정확한 PDF 계약이 없으므로 source sampling으로
  명시적 fallback한다.

### 실행·증거 필드

- UI: `Ray tracing > Run Options > Advanced > Primary ray sampling`
- `primary_sampling_contract=receiver_directed_primary_mis_v1`
- `requested_primary_sampling_strategy`
- `primary_sampling_strategy`
- `primary_sampling_fallback_reasons`
- `primary_sampling_directed_ray_count`
- `primary_sampling_weight_mean/min/max`
- `primary_sampling_effective_sample_ratio`

## Lambertian Bounce Receiver MIS

### 계산 방식

PERF-4E-B는 표면 hit마다 shadow ray를 하나 더 생성하는 전통적 NEE가 아니다.
기존 continuation ray 한 개의 방향 분포를 다음과 같이 바꾸는 단일-ray MIS다.

- 원래 Lambertian cosine 분포: `p_source = cos(theta) / pi`
- Receiver 면적 균일 표본을 solid angle PDF로 변환한 분포: `p_receiver`
- Receiver 제안 비율: `alpha`
- 혼합 분포: `q = (1-alpha)p_source + alpha p_receiver`
- 반사 Ray power 보정: `w = p_source / q`

기본 `alpha=0.5`에서 weight 상한은 2다. Receiver 방향으로 생성된 continuation
ray도 일반 반사 ray와 똑같이 BVH를 통과하므로, 중간 구조물이 있으면 Receiver보다
먼저 차폐면에 hit한다. 따라서 별도 shadow-ray 분기 없이 기존 차폐 판정을 그대로
재사용한다.

MIS가 켜진 ray의 `power_lumen`은 개별 광자의 물리 에너지가 아니라 Monte Carlo
가중 표본값이다. 일부 표본은 weight 때문에 원래 반사 power보다 커질 수 있지만,
여러 표본의 기대값은 원래 Lambertian 적분과 같아야 한다.

여러 Receiver가 활성화된 경우 Receiver를 균일 확률로 하나 선택하고, MIS PDF를
평가할 때는 같은 방향과 교차하는 모든 활성 Receiver proposal의 density를 합산한다.

### 지원·fallback 범위

- 지원: pure `lambertian` 표면, CPU object/SoA, Numba CPU, CUDA resident wavefront
- `specular`: 기존 delta 반사 경로 유지
- `gaussian`, `mixed`: 정규화된 방향 PDF 계약이 아직 없으므로 source sampling
  fallback
- Receiver 없음, legacy RNG, 비-SoA 경로: source sampling fallback
- 기본값: `source`; 실제 TV ROI 검증 전에는 자동 활성화하지 않음

### 실행·증거 필드

- UI: `Ray tracing > Run Options > Advanced > Reflected ray sampling`
- `bounce_sampling_contract=receiver_directed_lambertian_bounce_mis_v1`
- `requested_bounce_sampling_strategy`
- `bounce_sampling_strategy`
- `bounce_sampling_eligible_surface_count`
- `bounce_sampling_receiver_directed_fraction`
- `bounce_sampling_zero_weight_count`
- `bounce_sampling_unsupported_surface_count`
- `bounce_sampling_fallback_reasons`
- `bounce_sampling_weight_mean/min/max`
- `bounce_sampling_effective_sample_ratio`

## Auto convergence 표본 재사용

기존 `1→2→4→8배`는 각 단계를 처음부터 계산해 총 `15배` Ray를 처리했다. 새 계약은
독립 seed 구간 `1 + 1 + 2 + 4`를 계산하고 이전 표본을 누적하여 총 `8배`만 처리한다.

- 계약: `independent_segment_weighted_v1`
- 각 구간은 config seed와 명시적 Emitter seed를 모두 독립 seed로 파생한다.
- Flux grid는 구간 표본 수로 가중 평균한다.
- 제곱합은 `(N_segment/N_total)^2`로 변환해 합산한다.
- hit/count는 합산한다.
- contribution Flux는 표본 수 가중 평균, contribution count는 합산한다.
- Receiver 해상도·bin area·Receiver 집합이 실행 중 바뀌면 fail-closed로 누적을
  중단한다.

결과의 `metrics._convergence_accumulation`에 다음을 저장한다.

- `segment_rays`
- `segment_seeds`
- `segment_emitter_seeds`
- `segment_compute_states`
- `total_rays`
- `avoided_retrace_rays`

## 검증 명령

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4e_receiver_mis.py `
  --rays 20000 --repeats 12

.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4e_bounce_mis.py `
  --rays 20000 --repeats 12 --parity-rays 8192
```

## 2026-08-25 RTX 3070 synthetic 결과

1×1 mm Lambertian Emitter, 4×4 mm Receiver, 거리 100 mm 조건이다.

| 항목 | Source sampling | Receiver MIS |
| --- | ---: | ---: |
| 평균 Receiver hit | 11 | 10,023 |
| 평균 Flux | 0.00054993 lm | 0.00050985 lm |
| seed 간 상대 표준편차 | 30.61% | 0.382% |
| p50 runtime | 0.11063초 | 0.14632초 |

- 관측 variance reduction factor: 약 `7,460x`
- CPU/GPU 이산 결과 exact
- strict float64 최대 절대 오차: `8.88e-16`
- GPU preflight: RTX 3070, production Ray/BVH, FP64 PASS

두 평균 Flux 차이는 source 표본의 평균 hit가 11개뿐인 작은 표본 변동 범위다.
`projected_rays_for_5_percent`는 이 synthetic seed 분산의 단순 통계 환산이며 실제 TV
장면 성능 보장이 아니다.

## 2026-08-25 RTX 3070 반사광 synthetic 결과

폭 0.2 mm Gaussian Emitter가 z=10 mm의 Lambertian 반사판을 비추고, 반사점에서
20 mm 떨어진 1×1 mm Receiver가 광원 반대편에 있는 직접광 불가 장면이다.

| 항목 | Source bounce | Receiver bounce MIS |
| --- | ---: | ---: |
| 평균 Receiver hit | 17.17 | 10,009.58 |
| 평균 Flux | 6.8652e-4 lm | 6.3603e-4 lm |
| seed 간 상대 표준편차 | 20.30% | 0.384% |
| CPU p50 runtime | 0.11056초 | 0.13376초 |

- 작은각 근사 Flux `6.3662e-4 lm` 대비 MIS bias: `-0.092%`
- 관측 variance reduction factor: 약 `3,256x`
- 5×5 mm blocker 삽입 시 Receiver hit/Flux: `0 / 0 lm`
- CPU/GPU 이산 결과 exact
- strict float64 최대 absolute error: `3.55e-15`, 최대 ULP: `11`
- GPU preflight: RTX 3070, production Ray/BVH, FP64 PASS

동일 Ray 수에서 CPU 실행시간은 약 21% 늘었지만, 작은 Receiver의 목표 오차에
필요한 Ray 수 감소가 훨씬 컸다. 이 수치는 합성 장면의 분산 결과이며 실제 TV ROI
속도 보장이 아니다.

## 제한과 후속 단계

- Primary MIS는 광원에서 직접 보이는 Receiver, bounce MIS는 Lambertian
  반사점에서 보이는 Receiver에만 효과가 있다.
- Receiver가 해당 반사점에서도 완전히 가려진 장면에서는 Receiver proposal이
  차폐면으로 끝나므로 효율이 낮아질 수 있다. 원래 분포 표본을 유지하는 이유다.
- Gaussian·Mixed 표면은 정확한 정규화 PDF와 lobe별 MIS 계약을 만든 뒤 확장한다.
- delta specular는 일반 면적광 proposal과 직접 혼합하지 않고 별도 경로로 유지한다.
- 단일-ray MIS로 충분하지 않은 장면에서만 별도 shadow-ray NEE를 검토한다.
- 실제 TV ROI에서 source sampling 대비 Flux bias, peak-area error, heatmap 품질을
  여러 seed로 검증하기 전까지 기본값은 `source`로 유지한다.
