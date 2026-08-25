# PERF-4E Receiver 중요도 샘플링·표본 재사용 계약

## 목표

PERF-4E는 같은 Ray를 더 빠르게 계산하는 단계가 아니라, 작은 Receiver에서 목표
오차를 얻는 데 필요한 Ray 수 자체를 줄이는 단계다. 1차 구현은 다음 두 기능으로
구성한다.

1. Emitter primary Ray의 Receiver-directed MIS
2. Auto convergence의 독립 구간 표본 누적 재사용

표면 반사점에서 별도 shadow ray를 만드는 Next Event Estimation과 bounce MIS는
아직 구현하지 않았다. 차폐 뒤 반사광이 지배적인 빛샘 장면은 이 후속 단계가
필요하다.

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
- `primary_sampling_effective_sample_size_ratio`

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

## 제한과 후속 단계

- 현재 Receiver MIS는 primary 방향만 바꾼다.
- Receiver가 구조물 뒤에 완전히 가려진 장면에서는 직접 Receiver 제안이 차폐되어
  효과가 작거나 오히려 절반 표본을 낭비할 수 있다.
- 다음 PERF-4E-B는 Lambertian/Gaussian 반사점의 NEE 또는 bounce MIS와 shadow-ray
  차폐 판정을 GPU resident kernel에 추가해야 한다.
- delta specular는 일반 면적광 NEE와 직접 혼합하지 않고 별도 경로로 취급해야 한다.
- 실제 TV ROI에서 source sampling 대비 Flux bias, peak-area error, heatmap 품질을
  여러 seed로 검증하기 전까지 기본값은 `source`로 유지한다.
