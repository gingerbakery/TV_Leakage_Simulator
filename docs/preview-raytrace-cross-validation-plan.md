# Preview ↔ Ray Tracing 교차 검증 설계

2026-11-05. 설계 및 구현 범위 문서. 대표 경로/Receiver 기하 검증과 공통 센서 적분·양방향 비교를 구현했다(미커밋). 사용자 요청에 따라 비교 실행은 CUDA GPU 방식이다. 실제 GPU 검증은 아직 수행하지 못했다. 상세 범위와 검증 한계는 `changes/2026-11-05-finite-sensor-comparison.md`를 참조한다.

## 목적과 비교량

사용자 사례: 확산판 바닥 기준 후방 Uniform 방출, 100만 Ray, 최대 반사 10회, 같은 재질/Block. Preview 양성·forward Receiver 0 hit를 단순히 과검출/미검출로 분류하지 않는다. 현재 Preview는 평행 관찰 휘도(cd/m²), Receiver는 유한 면적/입체각에서 받은 flux(lm)를 계산하므로 직접 비교할 수 없다. hit 수는 별도 표본 진단이다.

검증 모드에 **공통 가상 센서**를 정의하고 두 방향 모두 같은 측정 함수의 적분을 추정한다. 면적·방향·Acceptance·ROI를 일치시키고 역추적도 고정 평행 방향 한 개가 아닌 동일 Acceptance 원뿔을 표본화한다. 원뿔 폭 0인 평행 관찰을 유한 원뿔 flux와 바로 비교하지 않는다.

공통 출력은 다음 flux다(거리 mm→m² 변환은 면적에서 한 번만).

`Φ = ∫(sensor area A) ∫(accepted Ω) L(x,ω) |n·ω| W(x,ω) dω dA`

`W`는 같은 ROI/마스크/방향 응답이다. forward는 실제 sensor hit의 가중 flux 합, reverse는 같은 면적/입체각의 샘플 PDF로 보정한 radiance 적분이다. uniform 면적/원뿔 샘플이면 reverse estimator는 `A·Ω·mean(L·cosθ·W)`다. Preview 중앙 방향 휘도와 원래 forward hit 수는 보조 출력으로 따로 보존한다.

## 1. 입력 고정과 측정영역 일치

Validation snapshot에 아래를 저장하고 SHA-256 manifest로 식별한다.

- CAD/ACC geometry, precision mode, 변환/제외, source face 매핑, Block 실제 삼각형과 반사 재질.
- 광원 삼각형·면적·방향·Uniform Sphere 각도 범위/축·normal flip·flux/휘도 정의. UI 이름이 같은지만 비교하지 않는다.
- BRDF/BSDF 원본·transport 설정·파장·정규화, 최대 반사 10회, epsilon, 전달 모델(현재 reflection-only).
- 센서 center/U/V/normal/normal flip, 면적·해상도·각도 원뿔·ROI·source sampling PDF.
- 종료 정책. 정량 검증의 baseline은 결정적 에너지 컷오프를 제거하고 양쪽에 명시한 unbiased roulette/max-depth 정책을 적용한다. 기존 생산 설정의 threshold run은 별도 비교해 컷오프 손실을 진단한다. 작업 프로젝트 설정은 덮어쓰지 않는다.
- 코드/계약/장치, discovery seed와 독립 validation seed 목록, 예산과 판정 기준.

생성 Receiver가 원래 관찰 footprint/대표 ray를 수용하는지는 기존 기하 검사로 먼저 확인한다. aperture와 무관한 더 넓은 Receiver를 달아 양성 결과를 만드는 방식은 사용하지 않는다.

## 2. 결정적 대표 경로 검사 — 저비용 첫 관문

기존 `경로 검증`을 활용한다. 현재 source 위의 시작점·발광 방향, 모든 반사 vertex의 CAD membership/forward BRDF 지원, segment 차폐, Receiver의 면적/normal/Acceptance/ROI를 검사한다. 광원→센서와 센서→광원의 기하 segment 순회를 비교하되, 비상호 BSDF 값은 단순히 입출력 방향을 바꿔 평가하지 않고 물리적인 light→observer 인자를 사용한다.

이 단계의 출력은 경로 존재/불일치 원인이다. 발견한 대표 ray를 다시 쏴 맞았다고 Monte Carlo hit나 측정 flux에 추가하지 않는다. 한 개 경로의 존재는 유한 면적·입체각에서 유의한 flux라는 증거가 아니다.

## 3. 동일 센서의 독립 정량 계산

### 독립 forward 기준

원래 광원 분포에서 전체 source 영역을 표본화한다. 발견 지점만 골라 쏘는 baseline으로 바꾸지 않는다. 현재 100만 Ray run의 seed 하나로 판정하지 않고 사전 고정한 독립 batch를 사용한다. Receiver flux·분산·hit·반사차수·컷오프·유효 표본 지표를 기록한다.

### 독립 reverse 기준

같은 센서의 area와 angular cone을 표본화한다. 각 경로에 camera/light/BRDF proposal과 roulette의 확률을 반영한다. 직접광/반사광, 전체 sensor와 후보 aperture별 flux를 따로 집계한다. 발견 단계에서 선정한 최고 각도를 validation 표본으로 다시 선정하지 않는다.

### 희귀 경로 보조 계산

기본 forward가 계속 0이면 receiver-directed/light-camera connection 등의 보조 추정기를 추가한다. 모든 proposal의 support와 PDF, delta lobe, MIS weight를 명시해 광량 편향을 막는다. 정확히 같은 대표 경로만 반복하는 계산은 정량 기준으로 사용하지 않는다. 순수 forward, reverse, guided estimator의 결과를 각각 표시하며 서로의 hit 목록을 합쳐 통과시키지 않는다.

CPU/GPU 같은 코드의 일치는 장치 구현 정합성 검사다. 서로 독립된 forward/reverse estimator와 해석해 검사는 별도로 둔다.

## 4. 사전 선언할 판정

| 결과 | 판정 |
|---|---|
| source/반사면/차폐/Receiver 관문 실패 | 조건 또는 경로 오류. flux 일치 검증 진행 금지 |
| 기하 유효, forward 0 hit | **정량 미확정**. 빛이 없거나 Preview가 틀렸다고 판정하지 않음 |
| 두 estimator의 불확도가 넓음 | 표본 부족/희귀 경로. 통과·실패 보류 |
| 동일 센서 flux의 차이가 사전 동등성 범위 안으로 충분히 제한됨 | 공통 측정량의 정량 일치 |
| 충분한 표본 후 차이가 동등성 범위 밖에 남음 | 광원 PDF/BRDF/정규화/종료/기하 구현 불일치 추적 |

검증 예산 예시는 독립 seed batch 16개와 단계별 4배 증액, 시간/최대 표본 상한이다. 값은 실행 전에 고정한다. 0 hit 또는 극소수 hit에서 표본 분산 0을 오차 0으로 해석하지 않는다. weighted 희귀 경로/대규모 weight에서는 단순 정규 근사를 신뢰하지 않고 미확정으로 둔다.

확률 검증은 평균의 차이 `Δ=Φforward−Φreverse`와 독립 batch 기반 불확도를 사용한다. 허용 오차 `τ=max(absolute_floor, relative_tolerance·reference_scale)`는 장면/단위/검출 목적에 따라 사전 선언한다. **차이의 구간 전체가 [-τ,+τ] 안에 들 때** 동등성 통과로 판정한다. 신뢰구간이 서로 겹친다는 이유만으로 통과시키지 않는다. 실제 TV 미보정 상태에서 임의의 절대 nit/lm 허용값을 물리 기준이라고 정하지 않는다.

## 5. 정상/음성/변형 대조 장면

| 장면 | 반드시 확인할 것 |
|---|---|
| 직접 보이는 큰 Lambertian 광원 | 원뿔 전체를 충분히 채우는 조건에서 `Φ=Areceiver·πL·sin²α` |
| 평행한 큰 후방 Uniform hemisphere 광원 | source가 센서 원뿔 footprint 전체를 채우면 `Φ=Φsource·(Areceiver/Asource)·(1−cosα)`; Lambertian과 혼용 금지 |
| 충분히 큰 이상 정반사 1회 | 동일한 가시 조건에서 mirror 반사율 ρ에 따른 해석해 |
| 실제 틈·홀 / 같은 자리의 닫힌 불투명 벽 | 열린 경로 양성·닫힌 직접 성분 0, 위치 오차와 차폐 검증 |
| 기존 반사형 Block으로 홀을 막음 | 해당 aperture의 통과 경로가 막힘. 반사로 다른 홀로 우회한 광량까지 0이라고 강제하지 않음 |
| 가상 광원·공중에 놓인 가짜 반사 vertex | 가상 source와 외관 구분 / 가짜 반사 vertex 거부 |
| Receiver 이동/뒤집기/Acceptance 축소 | 사전 예측한 수용 조건 변화와 일치 |
| 광원 0.5배/2배, 표본 수·seed 변경 | 기대 flux 스케일과 불확도 변화; 희귀 사건의 단일 run 단조성은 요구하지 않음 |
| 측정 BSDF의 전방/역방향 값이 다른 장면 | 동일 physical BRDF로 비교, 표를 전치해 맞추지 않음 |

위 해석해는 source/반사면이 전체 accepted cone을 가리고 다른 차폐가 없으며 단위·방향이 정의와 일치하는 합성 장면 전용이다. 복잡한 실제 TV에 그대로 대입하지 않는다.

## 6. 외관 Leak 위치의 검증

검증 보고에 (a) 실제 보이는 CAD 면, (b) 가상 발광면, (c) 실제 외장 개구부를 별도 필드로 둔다. 외장 부품 집합과 opening contour/부품 간 gap을 geometry provenance와 함께 정의해야 외관 위치를 확정할 수 있다. 확인된 aperture patch와 실제 유효 segment의 교점만 aperture marker로 쓴다. 이 patch는 위치 귀속용 센서이며 광선을 막는 가짜 기구물로 추가하지 않는다.

외곽 bounding box 교점, 평균점, 근처 면으로 임의 snap한 점은 외관 Leak 위치 증거로 인정하지 않는다. opening을 식별하지 못하면 `외관 위치 미확정`을 유지한다. 자동 aperture 추출은 별도 구현/검증 과제다.

## 7. 실물 TV 검증

계산끼리 맞아도 광원 등가 모델·재질·실제 gap이 틀리면 실물과 다를 수 있다. 실제 전원/휘도 설정, 관찰 위치/각도, fixture, 조도와 노출을 고정해 같은 aperture를 비교한다. 자동 노출/톤매핑/후처리를 기록한다. 사진의 밝기를 보정 없이 nit로 간주하지 않는다. 실측 가시/비가시 사례를 보관하고 새로운 모델의 과검출·미검출을 독립 holdout으로 평가한다.

## 구현 순서와 현재 상태

1. 구현됨(미커밋): 대표 경로 CPU 검사, Receiver footprint 수정, 저장 경로 기반 geometry-only 검사, 0 hit와 유효 대표 경로의 분리.
2. 구현됨(미커밋): 공통 sensor specification, reverse angular integration, 독립 seed/batch 비교 보고와 미확정 판정, GPU 실행 증거 검사, 실행/조회/중지 API 및 후보별 비교 UI. CPU 테스트는 수식·통합 검사용이며 실제 CUDA 실행 증거가 아니다.
3. 그다음: 적합한 재질/경로에만 PDF가 검증된 guided/bidirectional estimator, aperture 귀속, 실물 holdout.

GPU 측정 시 저장소 runbook의 production preflight와 실제 CUDA 성공 수 기준을 유지한다. 동일 장면 첫 run과 warm 2~3회, source snapshot/commit/SHA, fallback과 timing scope를 기록한다. 본 문서 작성으로 benchmark·GPU 실행·실물 검증이 수행된 것은 아니다.

## 기술 근거

- PBRT, Bidirectional Path Tracing: https://www.pbr-book.org/3ed-2018/Light_Transport_III_Bidirectional_Methods/Bidirectional_Path_Tracing
- PBRT, A Better Path Tracer (PDF/MIS 및 delta 처리): https://pbr-book.org/4ed/Light_Transport_I_Surface_Reflection/A_Better_Path_Tracer

본 프로젝트의 공통 센서/판정 방식과 위 합성 해석해 적용은 이 자료의 단순 인용이 아닌 별도 설계다. 구현 시 독립 수치/해석해 gate가 필요하다.
