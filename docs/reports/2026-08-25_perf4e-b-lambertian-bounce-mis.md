# PERF-4E-B Lambertian Bounce MIS 검증 보고서

## 판정

순수 Lambertian 반사광용 Receiver-directed bounce MIS는 합성 정확도·차폐·CPU/GPU
정합성 gate를 통과했다. 기존 결과를 바꾸지 않도록 기본값은 `source`로 유지한다.

## 환경

- 날짜: 2026-08-25
- GPU: NVIDIA GeForce RTX 3070
- Numba: 0.66.0
- Compute capability: 8.6
- production FP64 Ray/BVH preflight: PASS

## 장면

- Emitter: 0.2×0.2 mm, Gaussian 0.01°, +Z 방향
- Reflector: z=10 mm, Lambertian, reflectance 0.8
- Receiver: 1×1 mm, 반사점에서 20 mm, 광원 반대편
- 직접 Emitter→Receiver 경로: 없음
- Ray: seed당 20,000개, 12 seed
- Receiver proposal 비율: 0.5

## 결과

| 항목 | Source bounce | Receiver bounce MIS |
| --- | ---: | ---: |
| 평균 Receiver hit | 17.17 | 10,009.58 |
| 평균 Flux | 6.8652e-4 lm | 6.3603e-4 lm |
| Flux 표준편차 | 1.3934e-4 lm | 2.4417e-6 lm |
| 상대 표준편차 | 20.30% | 0.384% |
| CPU p50 runtime | 0.11056초 | 0.13376초 |

- 관측 variance reduction factor: `3,256.5x`
- 작은각 근사 Flux: `6.3662e-4 lm`
- MIS 상대 bias: `-0.0919%`
- 평균 Receiver-directed fraction: `50.010%`
- 평균 effective sample ratio: `50.032%`

## 차폐 검증

반사판과 Receiver 사이에 5×5 mm 흡광 blocker를 삽입했다. Receiver 방향으로
제안된 ray가 Receiver를 무시하고 통과하지 않았으며 결과는 다음과 같다.

- Receiver hit: `0`
- Receiver Flux: `0 lm`
- 판정: PASS

## CPU/GPU 정합성

8,192 Ray 동일 seed에서 CPU host-roundtrip과 GPU resident를 비교했다.

- discrete difference: `0`
- strict float64: PASS
- 최대 absolute error: `3.55e-15`
- 최대 relative error: `1.88e-15`
- 최대 ULP: `11` (`32` 허용)
- GPU resident fallback: 없음

GPU runtime `3.84초`는 해당 프로세스의 cold JIT를 포함하므로 가속 수치로
사용하지 않는다.

## 해석

동일 Ray 수의 CPU 계산은 약 21% 늘었지만, 작은 Receiver에서 seed 분산이 약
3,256배 줄었다. PERF-4E-B의 목적은 Ray 하나의 계산시간 감소가 아니라 목표
오차에 필요한 Ray 수 감소다.

## 제한

- pure Lambertian 표면만 Receiver proposal을 사용한다.
- Specular는 delta 경로, Gaussian·Mixed는 source fallback이다.
- 실제 TV ROI의 복잡한 다회 반사·차폐 구조에서는 효과가 달라질 수 있다.
- 운영 기본 활성화는 실제 도면 여러 seed 비교 후 결정한다.

원본: `outputs/perf4e_bounce_mis/benchmark.json`
