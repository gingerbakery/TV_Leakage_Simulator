# PERF-4E Receiver MIS 검증 보고서

## 환경·장면

- 날짜: 2026-08-25
- GPU: NVIDIA GeForce RTX 3070
- production FP64 Ray/BVH preflight: PASS
- Emitter: 1×1 mm Lambertian
- Receiver: 4×4 mm, 거리 100 mm
- Ray: seed당 20,000개, 12 seed
- Receiver-directed 비율: 0.5

## 통계 결과

| 항목 | Source | Receiver MIS |
| --- | ---: | ---: |
| 평균 hit | 11 | 10,023 |
| 평균 Flux | 5.4993e-4 lm | 5.0985e-4 lm |
| Flux 표준편차 | 1.6831e-4 lm | 1.9486e-6 lm |
| 상대 표준편차 | 30.61% | 0.382% |
| warm p50 | 0.11063초 | 0.14632초 |

- 분산 감소: 약 7,460배
- MIS 자체의 실행 시간은 약 32% 증가했지만 필요한 표본 수 감소 폭이 훨씬 컸다.
- CPU/GPU 이산 결과 exact, strict float64 PASS
- 최대 absolute error `8.88e-16`, 최대 ULP 6

## Auto convergence 재사용

- 새 구간: `1 + 1 + 2 + 4 = 8배`
- 기존 전체 재실행: `1 + 2 + 4 + 8 = 15배`
- 8배 도달 시 재계산 방지: base Ray의 7배, 기존 처리량 대비 46.7%
- Flux·제곱합·hit·contribution을 누적 결합하는 frontend 단위 테스트 통과
- config seed와 Emitter seed의 구간별 독립화 테스트 통과

## 100k 확장 확인

100,000 Ray×8 seed에서 source 평균 hit는 54.4, MIS 평균 hit는 50,049였다.
상대 표준편차는 `14.45% → 0.167%`, 관측 분산 감소는 약 `8,535x`였다.
CPU/GPU 이산 결과 exact, strict float64 최대 absolute error `3.33e-15`를
확인했다.

## 판정과 제한

Primary Receiver MIS와 convergence sample reuse는 1차 통과했다. 현재 결과는
Receiver가 직접 보이는 synthetic 장면이다. 차폐 뒤 반사광용 surface NEE/bounce
MIS는 아직 없으므로 실제 TV ROI의 5% error 달성 시간을 이 수치로 보장하지 않는다.

원본: `outputs/perf4e_receiver_mis/benchmark.json`
확장 원본: `outputs/perf4e_receiver_mis/benchmark_100k.json`
