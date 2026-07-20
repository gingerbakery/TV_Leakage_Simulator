# RT-2A Gap Sweep 및 차폐 그래픽 개선

## 요청 배경

- 기존 차폐 판정 그림에서 blocker가 배경과 비슷해 차폐 구조를 즉시 알아보기 어려웠다.
- 4 mm 단일 gap 결과만으로는 gap 크기와 투과량의 관계를 해석하기 어려웠다.
- Receiver 결과를 단일 수치뿐 아니라 공간 분포 heatmap으로 비교할 필요가 있었다.

## 그래픽 개선

- CAD blocker를 검정색 면과 황색 해치/경계선으로 표시한다.
- 완전 차폐 구조에 `CAD BLOCKER` 라벨을 표시한다.
- 부분 개구 구조에 `4 mm GAP` 치수 위치를 표시한다.
- Receiver 도달 ray는 녹색, CAD 차단 ray는 주황색을 유지한다.

## Gap sweep 조건

- Gap 크기: `0, 0.5, 1, 2, 3, 4, 6, 8, 12, 24 mm`
- Emitter 크기: `2 × 2 mm`
- Emitter와 blocker 거리: `10 mm`
- Emitter와 Receiver 거리: `20 mm`
- 방향 분포: normal 중심 Gaussian, `sigma = 8°`
- Ray 수: gap당 `10,000`
- 난수 seed: `20260716`
- 반사/산란: 미적용

## 결과

| Gap | Receiver hit | CAD blocked |
|---:|---:|---:|
| 0 mm | 0.00% | 100.00% |
| 0.5 mm | 18.49% | 81.51% |
| 1 mm | 36.25% | 63.75% |
| 2 mm | 66.26% | 33.74% |
| 3 mm | 82.11% | 17.89% |
| 4 mm | 91.00% | 9.00% |
| 6 mm | 98.04% | 1.96% |
| 8 mm | 99.65% | 0.35% |
| 12 mm | 99.99% | 0.01% |
| 24 mm | 100.00% | 0.00% |

`4 mm → 91%`는 위 합성 조건에서 얻은 결과다. 모든 4 mm 기구 gap의 일반적인 투과율을 의미하지 않는다. 실제 결과는 광원 크기·거리·방향 분포·gap 형상·Receiver 위치에 따라 달라진다.

## Heatmap

- `0, 1, 2, 4, 8, 24 mm` 결과를 동일한 `nit_est` color scale로 비교한다.
- gap이 증가하면 Receiver 도달 면적과 총 flux가 함께 증가하는 경향을 확인했다.
- 결과 파일: `outputs/rt2a_occlusion_report/rt2a_gap_sweep_report.png`
- 원시 수치: `outputs/rt2a_occlusion_report/summary.json`

## 테스트 보강

- gap 증가에 따라 Receiver hit가 단조 증가하는 회귀 테스트를 추가했다.
- 부분 gap flux가 완전 차폐와 완전 개방 사이에 위치하는 테스트를 추가했다.
- RT-2A 전용 테스트는 4개에서 6개로 증가했다.
- 현재 전체 자동 테스트는 16개이며 모두 통과한다.

## 충분성 판단

- 16개 테스트는 RT-2A 알고리즘 골격의 회귀 방지에는 충분하다.
- 실제 제품 적용 또는 release 판단에는 충분하지 않다.
- 후속 필수 검증은 off-axis emitter, 비대칭 gap, 여러 blocker, 얇은/겹침 면, 실제 STEP CAD, transform 전후 차폐, ray 수 수렴성이다.

## 프로그램 UI 확장안

- 현재 UI는 활성 형상 한 건의 Receiver heatmap을 표시할 수 있다.
- 여러 gap을 한 번에 비교하려면 별도 `Gap Sweep` 실행 모드가 필요하다.
- 입력 후보: 대상 component, 이동 축, 시작/종료 gap, step, ray 수.
- 출력 후보: gap별 hit/blocked 곡선, gap별 `nit_est` heatmap, peak/mean/p95 변화, CSV/PNG export.
