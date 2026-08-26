# 2026-08-25 PERF-4E-B Lambertian Bounce MIS 변경 이력

## 목적

작은 Receiver가 광원에서 직접 보이지 않고 Lambertian 반사를 거쳐야 하는 장면에서
무작위 반사 Ray 대부분이 Receiver를 놓치는 문제를 줄인다. 기대 광량을 바꾸지
않으면서 Receiver hit 표본을 늘려 목표 오차에 필요한 Ray 수를 낮추는 것이 목적이다.

## 구현

- `RayTraceConfig`에 다음 설정을 추가했다.
  - `bounce_sampling_strategy`: `source` 또는 `receiver_mis`
  - `bounce_receiver_importance_fraction`: 기본 `0.5`
- 순수 Lambertian 반사점에서 원래 cosine PDF와 Receiver 면적 proposal PDF를
  혼합하고 `p_source / q` weight를 반사 power에 적용한다.
- CPU reference, Numba CPU counter planner, CUDA resident wavefront에 같은
  semantic RNG lane과 계산 계약을 적용했다.
- Receiver 방향 continuation ray도 기존 BVH를 통과시켜 차폐 구조물을 정상 판정한다.
- Specular는 기존 delta 경로를 유지하고 Gaussian·Mixed는 source sampling으로
  명시적 fallback한다.
- GPU resident 결과에 eligible/direct/zero/unsupported count와 weight 통계를
  추가하고 상위 성능 summary에 집계한다.
- Advanced UI에 `Reflected ray sampling`과 Receiver proposal 비율 설정을 추가했다.

## 데이터·진단 계약

- 계약명: `receiver_directed_lambertian_bounce_mis_v1`
- 새 RNG lane: `128`~`131`
- 기본값은 기존 결과 호환을 위해 `source`다.
- weight는 유한·비음수이며 상한은 `1/(1-alpha)`다.
- 미지원 표면은 `bounce_sampling_fallback_reasons`와
  `bounce_sampling_unsupported_surface_count`에 기록한다.

## 검증

- Python 전체 테스트: `320 passed`
- Frontend 테스트: `158 passed`
- Frontend typecheck/lint/build: 통과
- PERF-4E-B 전용 테스트: `5 passed`
  - Numba/reference exact
  - 반사광 분산 감소
  - blocker 차폐
  - Gaussian fallback exact
  - CPU/GPU discrete exact + strict float64
- RTX 3070 production Ray/BVH preflight: 통과

## 성능·정확도 결과

20,000 Ray×12 seed의 직접광 불가 Lambertian 반사 synthetic 장면:

- Source 평균 hit: `17.17`
- Bounce MIS 평균 hit: `10,009.58`
- 상대 표준편차: `20.30% → 0.384%`
- 관측 분산 감소: 약 `3,256x`
- 작은각 근사 대비 MIS Flux bias: `-0.092%`
- CPU p50: `0.11056초 → 0.13376초`
- blocker 삽입 시 Receiver hit/Flux: `0 / 0 lm`
- CPU/GPU 최대 absolute error: `3.55e-15`, 최대 ULP: `11`

## 제한·후속

- 합성 장면 결과를 실제 TV ROI 성능으로 해석하지 않는다.
- Gaussian·Mixed는 정규화 PDF 계약을 구현하기 전까지 가속 대상이 아니다.
- 실제 TV ROI에서 여러 seed의 Flux, peak-area error, heatmap 품질과 1억 Ray
  장시간 VRAM·열 안정성을 별도로 검증한다.
- 별도 shadow-ray NEE는 단일 continuation-ray MIS가 부족한 장면에서 재검토한다.
