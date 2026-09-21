# RT 내부 정확도 정밀 점검 및 모델 간 광량 비교 기준

작성: 2026-09-21

추가 요청 반영: ROI 면 속성 연결 수정 직후, 기본/사용자 지정 Surface property의
전달 및 반사 계산을 재검증하는 **Phase A-S**를 신설했다. 같은 날 실행을 시작해
ROI 연결 오류와 0% 반사율의 집계 오류를 수정했다. 상세 실행 범위는
`changes/2026-09-21_surface-transport-audit.md`에 기록한다.

## 범위와 현재 상태

LT 실행은 별도 작업에서 진행한다. 여기서는 입력 계약, 광선 수송, 집계,
단위/표시, 통계 판정을 각각 검증한다. CPU/GPU가 같다는 사실만으로 물리적
정확성을 보장하지 않으며, 통계적 허용범위와 엄격 경로 일치는 별도 기준이다.

이 문서는 **전체 정밀 검증 완료 보고서가 아니다**. 최초 진단 이후 A/A-S의
핵심 합성 실험, B/C의 광량·종료 실험 및 D의 좌표 변환 검사를 실행했다.
확인된 제품 오류를 수정했으며 서버 재시작, 커밋, push는 하지 않았다.

- 검사 소스: `main`의 `e887d68c1b90af7534a8926c7e9c8209c17a34a0` 및 기존 미커밋 통계 판정 수정.
- 전달 경로: source checkout, 기존 `.venv-gpu`, RTX 3070.
- production CUDA preflight와 실제 GPU 실행 증거를 새로 확인했다.
- 최초 재현: `outputs/rt_accuracy_audit_2026-09-21/audit.py`, `audit.json`.
- 후속 실험: 같은 폴더의 `transport.json`, `angular.json`, `termination.json`, 회귀 로그.
- 이전 실제 장치 검증: 별도 `rt_internal_finish_2026-09-21` 보고서의 실행 증거 참조.

## 1. 이번에 확인한 사항

### A. ROI/부품 제외 이후 face override 연결 오류 — 재현 후 수정

`raytrace_bridge.py`는 optical assignment의 원본 면 번호를 해석 mesh 번호로
변환한다. 반면 `optics.py`의 resolver는 metadata의 `source_face_index`로
찾는다. 원본/해석 번호가 달라지면 override를 놓치거나 잘못된 면에 연결할
위험이 있다. 아래는 누락이 실제 광량에 영향을 주는 재현이다.

입력 1 lm, 64 Ray, 1회 경면, override 반사율 90%, 기본 반사율 10%.
광선과 무관한 부품만 제외하므로 정상 광학 결과는 같아야 한다.

| 조건 | 원본/해석 면 번호 | 실제 적용 | 수광량 |
|---|---|---|---:|
| 전체 형상 | 1 / 1 | face override 90% | 0.9 lm |
| ROI에 원본 면 1만 유지 | 1 / 0 | 기본 물성 10% | 0.1 lm |
| 다른 부품 7 제외 | 1 / 0 | 기본 물성 10% | 0.1 lm |

CPU scalar와 batch에서 모두 재현했다. Face emitter의 geometry 번호 변환은
필요하므로 optical assignment와 구분해야 한다. 기존 테스트 중에는 변환된
번호만 확인하고 최종 resolver/수광량을 확인하지 않는 공백이 있다.

후속 수정에서는 optical assignment의 원본 번호를 유지하고 ROI 밖 번호만
제외했다. 위 세 조건 모두 0.9 lm으로 복구되었고, 인접 면의 override 오연결,
정밀 mesh의 면 번호 확장, 캐시 재사용, `.bitsam` 복원/편집 회귀도 확인했다.

### B. 고정 최소 에너지 종료와 Ray 수 의존성 — 절단 편향 확인

총광속을 고정하고 Ray 수를 늘리면 각 Ray의 광속은 감소한다. 절대 광속
임계값으로 약한 Ray를 제거하면 표본만 늘렸는데도 수광량이 달라질 수 있다.

반사율 80%와 50%인 두 경면, 이론 수광량 0.4 lm:

| Ray 수 | 최소 에너지(lm/Ray) | 수광량 |
|---:|---:|---:|
| 1,000 | 0 | 0.4 lm |
| 2,000 | 0 | 0.4 lm |
| 1,000 | 0.0003 | 0.4 lm |
| 2,000 | 0.0003 | 0 lm |

효과를 분리하기 위해 기본값보다 큰 임계값을 의도적으로 사용했다. 이것은
현재 사용자 모델이나 기본 `1e-9`에서 같은 크기의 오류가 났다는 증거는 아니다.
종료 정책의 구조적 편향이며 단순 산술 오류와 구분한다. 실제 기본값, 입력
광속, Ray 수, 반사율을 함께 sweep하고 누적 segment 분할도 검사해야 한다.
기존 Russian roulette는 생존 가중치와 분산을 별도 검증한 뒤 대안으로 평가한다.

### C. 측광 환산 — 단순 산술 검사 통과, 실제 휘도 모델은 별개

현재 backend `_build_direct_metrics`와 frontend heatmap은 다음 식을 사용한다.

```text
E_cell [lx] = Phi_cell [lm] / (A_cell [mm²] × 1e-6)
L_est [nit] = k_abs × k_brdf × E_cell / pi
```

기본 계수는 `k_abs=0.12`, `k_brdf=1.0`이다. 두 셀에 각각 1e-6/3e-6 lm,
각 셀 면적 1 mm²를 주면 평균 2 lx, Peak 3 lx, 평균 추정 0.076394 nit,
Peak 추정 0.114592 nit가 나왔다. 계수를 두 배로 하면 nit만 두 배가 되며,
광속/lux는 그대로다. 면적을 두 배로 하면 광속은 같고 lux/nit가 절반이다.
이 검사는 단위 환산/비례 관계만 확인하며 임의 형상의 실제 휘도를 검증하지 않는다.

수광면 교차에서 광속 packet을 다시 cosine으로 감쇠하지 않는다. 입사각은
교차 위치/투영과 acceptance gate에 반영된다. 관련 scalar/numeric 및
acceptance 회귀 4개와 Receiver 통계 회귀 6개를 실행하여 총 10개 통과했다.
이 통과가 ROI 물성 오류나 새로운 GPU 조합의 통과를 의미하지는 않는다.

### D. Set Luminance는 총광속 입력의 Lambertian-equivalent 기준

`EmitterSpec.effective_power_lumen`은 `Phi = pi × L_set × A_m2`를 사용한다.
500 nit에서 10×10 mm는 0.157080 lm, 20×20 mm는 0.628319 lm이다.
Gaussian도 같은 면적이면 같은 총광속으로 환산된다.

한쪽 반구의 균일 Lambertian 면광원에서는 물리적 의미가 있지만, Aim Area /
Aim Sphere / Gaussian에 같은 총광속을 재분배하면 각 방향의 실제 휘도가
모두 입력 nit와 같다는 뜻이 아니다. 입력 nit를 무조건 TV set 사양과 동일시하지
않고 `Lambertian-equivalent source setting`이라는 계약을 명확히 해야 한다.
현재 코드에 이미 equivalent 주석이 있으며 수식 자체를 새 버그로 분류하지 않는다.

### E. 낮은 기본 반사율도 입사각 보정 시 높아질 수 있음

`effective_surface_reflectance`는 근사적인 grazing boost를 사용한다. 예시로
base 3%, roughness 0인 specular에서 입사각 89°이면 약 88.50%를 적용한다.
roughness 1에서는 같은 각도에서 약 24.37%다. 이는 코드의 현재 동작이며
실제 Black PC가 이 반사율이라는 뜻이 아니다. 함수의 이름/모델 일치가 아닌
측정 BSDF와의 물리 모델 적합성을 별도 확인해야 한다. 따라서 낮은 base
reflectance만으로 고반사 경로가 없다고 단정하지 않는다.

## 2. nit를 모델 간 절대 기준으로 바로 쓰기 어려운 이유

조도는 면에 들어오는 광속의 면적 밀도이며, 휘도는 지정된 방향과 투영 면적을
포함한다. 수광면에 같은 광속이 들어와도 방향 분포가 다르면 관측 휘도는
달라질 수 있다. 현재 공간 격자 집계는 방향별 휘도 분포를 저장하지 않는다.

`rho × E / pi`는 이상적 Lambertian 반사면에 적용할 수 있는 관계이지,
빈 공간의 가상 Receiver나 임의 경면/집광 구조에 공통 적용하는 환산법이 아니다.
현재 계수 곱을 모든 모델의 실제 반사율이나 측정기 전달함수로 간주하면 안 된다.

한 형상에 맞춘 단일 계수로 다른 모든 형상의 실측 nit를 맞출 수 있다고
보장하지 않는다. 같은 양의 계수를 쓰면 lux와 추정 nit의 상대 순위는 같으므로,
단위 표시만 변경해도 RT의 입력/수송 오류가 해결되는 것은 아니다.

## 3. 권장 결과/비교 지표 — UI 변경 전 제안

| 지표 | 정의와 목적 | 필요한 조건 |
|---|---|---|
| 수광 광속(lm, 작은 값은 µlm) | 지정 Receiver에 들어온 총광속 | 같은 수광 범위/위치/방향/각도 |
| 수광 광속비(%, ppm) | Phi_receiver / Phi_emitted | 활성 광원의 실제 총광속 분모; Ray 도달률과 구분 |
| 평균 조도(lx) | Phi_receiver / A_receiver | 같은 물리 면적과 수광 조건 |
| 고정 면적 최대 평균 조도(lx) | 미리 정한 측정창의 평균 중 최댓값 | 모든 모델에 같은 mm×mm 창, 위치 검색 규칙 |
| Raw Peak 조도(lx) | 가장 높은 단일 셀 | 셀 크기/격자 정렬/표본 오차 함께 표시 |
| 추정 휘도(nit_est) | 기존 보정식 결과 | 보조 지표, 계수/가정/미보정 상태 공개 |

- 입력 총광속이 다르면 `lx / emitted lm`도 기록한다. Receiver 위치와 광원
  방향/면적이 다른 조건을 정규화 하나로 같은 물리 문제로 만들 수는 없다.
- 형상 비교는 총광속 1 lm 정규화 기준과 실제 사용 조건을 분리하여 보고한다.
- Set Luminance가 같아도 발광 면적이 다르면 입력 lm이 다르다.
- 입력 광속비는 선택된 Receiver 포획 비율이며 모든 방향의 총 빛샘률이라고
  부르지 않는다. 여러 Receiver는 겹침/첫 hit 종료/이중 계수 규칙을 고정한다.
- P95는 전체 면의 대부분이 어두울 때 국부 hotspot을 놓칠 수 있으므로
  고정 면적 hotspot 지표의 대체재로 무조건 쓰지 않는다.
- 히트맵 비교는 동일한 절대 색상 범위를 사용하고 raw/평활 결과를 구분한다.
- Ray 수 자체를 반드시 같게 할 필요는 없지만 독립 seed와 신뢰구간으로
  비교 정밀도를 맞춘다. grid 해상도와 정렬은 같게 유지한다.

## 4. 정밀 검증 작업표

| 단계 | 검증 내용 | 종료/판정 기준 | 상태 |
|---|---|---|---|
| A | ROI/제외/transform/저장 복원 이후 face override와 광원 면 ID | 지정 물성/실제 광량 일치, cached/fresh 및 CPU/GPU 대조 | 핵심 연결 오류 수정·합성 회귀 통과; 실제 복잡 CAD 확장 대기 |
| A-S | 기본/사용자 지정 면 속성의 UI→해석 전달 및 실제 반사율·분포 재검증 | Gloss/Matte 등 실제 preset 값, 우선순위, 반사 광속비, Specular/Lambertian/Gaussian/Mixed 분포를 독립 기준으로 대조 | catalog→요청→해석, 독립 분포/광량 실험 통과; 극단 sigma·UI 수동 전수 점검 별도 |
| B | 광량·면적·단위·Set Luminance·multi-emitter 합산 | 독립 수식 대조, 0/배율/면적, 해석 총광속 ledger | 환산·배율·다중 광원 통과, 0% 표면 집계 수정; 일반 에너지 ledger 추가 필요 |
| C | Ray 수/총광속/segment/최소 에너지/반사 상한 | 절단 편향과 표본 노이즈 분리, 종료 잔여 광량 기록 | 상대 종료 기준·절단 진단 추가, 228회 통제 실험 및 CPU/GPU 114쌍 통과; 완전한 가중 에너지 ledger는 남음 |
| D | 미세 틈/공유 모서리/접선/얇은 판/곡면 법선 | scale/translation/rotation/mesh refinement 대조; BVH vs brute-force | 2회 반사 좌표 변환/BVH 대조 24조건 통과; 미세 틈·곡면 전수 대기 |
| E | CPU/GPU 최초 분기 지점 | 동일 Ray의 교차·normal·RNG·lobe·weight 추적, strict와 통계 gate 별도 | 이전 고심도 분기 미해결 |
| F | A-S에서 확인한 반사 모델의 복잡 형상·다회반사·각도 보정 조합 | 단일 면 통과와 별도로 곡면/틈/혼합 물성의 광속 보존·분포·수렴 검증 | 기존 다회반사/1,000회 회귀 재실행 통과; 복합 정밀 sweep 대기 |
| G | source/MIS·roulette·batch·누적·fallback 조합 | 사전 정의 독립 seed 통계 기준; 가중치/제곱합/zero hit 검사 | 기존 MIS/fallback 회귀 통과, roulette 3 seed 예비 확인; 복합 실험 대기 |
| H | Receiver 수광각/전후면/중첩/경계/셀·Peak | 독립 수광 기준, mm²→m², 단일 광선 기여와 cutoff 확인 | 전방위 반사 포획·기존 수광각/통계 회귀 통과; 측정 모델 확장 대기 |
| I | UI와 실행 mesh·캐시·저장/불러오기·이전 결과 | geometry/profile/설정 fingerprint와 결과 연결, stale 결과 방지 | 합성 scene의 정밀 mesh/캐시/파일 복원·재편집 통과; 실제 CAD/UI 통합 추가 필요 |

우선 실행 순서는 **A 수정/회귀 → A-S → B/C → D/E → F/G → H/I 통합 검증**이다.
A-S의 preset 목록화와 독립 기준 준비는 A 수정과 병행할 수 있지만, ROI 이후
면 속성 전달을 확인하지 못한 결과로 A-S 전체 통과를 선언하지 않는다.

2026-09-21 C 후속 검증에서 상대 종료 기준, 절단 진단, MIS 0가중치 종료 오류를
수정했다. 기존 파일은 절대 기준을 보존한다. 상세 내용과 미완료 범위는
`changes/2026-09-21_termination-policy-validation.md`를 참조한다.

현재 다음 우선순위는 **D/E의 복잡 형상 최초 분기 추적 → C/B의 가중 에너지
ledger 확장 → F/G/H/I의 복합 검증**이다. 임계값을 임의 변경하거나 엄격
CPU/GPU 비교 허용오차를 넓혀 미해결 항목을 통과 처리하지 않는다.

### 4.1. Phase A-S — 면 속성 전달 및 반사 물리 재검증

목적은 "화면에 Gloss/Matte 또는 60%라고 표시된다"를 확인하는 데 그치지 않고,
**사용자가 의도한 면에 실제로 어떤 수치/분포가 적용되어 얼마의 빛이 어느 방향으로
반사되는지**까지 끝에서 끝으로 검증하는 것이다. 기존 RT-2B/2C 단위 테스트는
참고 자료이며 이번 재검증의 대체 증거로 쓰지 않는다.

| 하위 단계 | 검증 대상 | 확인 방법/기대 결과 |
|---|---|---|
| A-S1 | 기본 Gloss/Matte 등 전체 preset 및 사용자 지정값 | 표시명·ID·반사율·scatter model·roughness·Gaussian sigma·혼합 비율을 실제 catalog에서 목록화하고 UI, 요청 payload, backend profile, 실제 충돌 적용값을 대조 |
| A-S2 | 부품 기본값과 면 override의 우선순위 | 같은 부품의 서로 다른 면에 서로 다른 물성을 부여하고 면별 실제 적용값/수광량 확인; 미지정/비활성/삭제/알 수 없는 profile의 fallback도 명시 |
| A-S3 | 반사율과 광속 감쇠 | 각도 보정 OFF에서 표면 입사 광속 대비 전체 반사 광속을 독립 이론값과 대조; 60% 입력이 내부 0.6으로 정확히 전달되는지 포함 |
| A-S4 | 반사 방향/산란 분포 | Specular, Lambertian, Gaussian, Mixed를 방향 히스토그램·독립 적분·방위각/극각 통계로 확인; 반사 광량과 분포 검증을 분리 |
| A-S5 | 각도 보정 ON/OFF와 거칠기 | 기본 반사율과 유효 반사율을 분리 기록하고 입사각/roughness에 따른 변화, 범위, 연속성, 경계값을 검증 |
| A-S6 | 실행 경로 및 재설정 회귀 | CPU scalar/batch·실제 GPU, ROI 전후, transform, 제외, cached/fresh, 저장/복원, 속성 재편집을 대조 |

#### 기본값/사용자 입력의 전달 계약

- Gloss가 반드시 Specular, Matte가 반드시 Lambertian이라고 이름만으로
  가정하지 않는다. 현재 preset의 실제 수치와 의도한 물리 모델을 대조한다.
- 기본값을 그대로 사용한 경우, 반사율만 변경한 경우, 분포만 변경한 경우,
  sigma/거칠기/혼합비를 변경한 경우를 나누어 검사한다. 하나를 편집했을 때
  다른 필드가 초기화되거나 이전 preset 값으로 돌아가는지도 확인한다.
- 우선순위 `face override → part assignment → mesh material → default →
  unassigned`를 테스트하며, 같은 component의 인접 면끼리 설정이 섞이지 않아야 한다.
- UI 원본 면 ID, ROI/transform 이후 source/trace 면 ID, profile ID와 최종
  적용값을 같은 기록으로 추적한다. UI 색상은 광학 물성과 별도이며 Display
  color만 변경해도 광량이 변하지 않는지 음성 대조 조건으로 확인한다.
- `.bitsam` 저장/복원 및 재편집 이후에도 동일한 입력 계약이 유지되어야 한다.

#### "반사율 60%면 실제 60%인가"의 독립 판정

기준 실험은 MIS/roulette/각도 보정 OFF, 에너지 임계값 0, 충분한 반사 상한,
정의된 단일 반사면으로 구성한다. 입력 광속 중 실제 해당 면에 입사한 양을
분모로 사용한다. 광원에서 빗나간 광량을 반사 손실로 계산하지 않는다.

```text
전체 반사 광속 / 해당 면 입사 광속 = 0.60
1 lm 입사 → 0.6 lm 반사
동일 60% 면에서 정확히 2회 반사 → 0.36 lm
R1=60%, R2=30% 면에서 정확히 2회 반사 → 0.18 lm
```

반사 후 모든 유효 출사 방향을 포획하는 수광 구조 또는 독립적인 경계 광속
집계를 사용한다. 작은 Receiver 하나에는 일부만 도달할 수 있으므로 그 값이
0.6 lm보다 작다고 반사율 오류로 판정하지 않는다. 포획면의 겹침, 자기 차폐,
입사 광선 혼입과 중복 집계를 제거한다. 같은 내부 반사율 함수를 사용해 만든
summary 숫자끼리의 비교만으로 통과시키지 않는다.

반사율 0/3/14/60/95/99.9/100%를 포함하고, Specular/Lambertian/Gaussian/Mixed
각각에서 출사 광속 합과 손실 ledger를 확인한다. 투과를 다루지 않는 이 기준
실험에서 반사되지 않은 몫은 모델의 비반사 손실이며 Receiver 누락과 구분한다.
100% 조건은 유한 반사 통로로 제한하고 상한 종료를 광학 손실로 숨기지 않는다.

각도 보정 ON에서는 기본값 60%와 유효 반사율이 다를 수 있다. 이 경우 60%를
무조건 기대하지 않고 **각도별로 사전 정의한 식/기준값**과 대조한다. 실제
재질에 대한 근사 모델의 타당성과 코드가 정해진 식을 구현하는지는 별도 판정한다.

#### 분포 검증 기준

- **Specular:** 반사 법칙의 방향/단위 벡터/출사 반구를 확인한다. 정면 및
  30/60/85/89° 입사, 회전된 면, 앞/뒷면 법선 계약을 포함한다.
- **Lambertian:** 출사 입체각 밀도 `p(omega)=cos(theta)/pi`, 방위각 균일성,
  극각 누적분포 `P(theta<=t)=sin²(t)`, `mean(cos(theta))=2/3`을 독립 기준으로
  사용한다. 각도 histogram은 입체각/투영 가중치를 혼동하지 않도록 정의한다.
- **Gaussian:** sigma의 의미(극각/접평면 축별 폭), degree/radian 변환,
  경면축 중심, 표면 아래 방향 제거, 큰 각도 tail, 재시도 제한과 fallback의
  규칙을 먼저 명문화한다. 독립 적분/표본 기준과 0에 가까운 폭 및 3/12/30°
  등의 분포를 비교한다. 경사 입사에서 hemisphere clipping으로 달라지는
  정규화와 경면축에 인위적으로 쌓이는 확률 질량도 확인한다. 다른 툴의
  Gaussian 이름과 같다는 이유로 같은 분포라고 가정하지 않는다.
- **Mixed:** 순수 경면/산란의 양 끝값과 중간 혼합비에서 lobe 선택 비율,
  각 lobe의 광량, 전체 광속 합을 확인한다. 비율 정규화나 표본 가중치 때문에
  반사율이 중복 적용되거나 에너지가 증가하지 않아야 한다.
- **광원 분포와 구분:** 우선 동일한 좁은 방향 광원으로 반사면만 바꾸어 검사한다.
  그다음 Lambertian/Gaussian/Aim Area/Aim Sphere 광원을 조합하여 source
  sampling과 surface scattering의 오류를 혼동하지 않도록 한다.

#### 판정과 보고 산출물

결정적 이론 실험과 확률 분포 실험의 오차 기준을 분리하고 실행 전에 고정한다.
동일 seed 반복과 독립 seed 통계 검사를 모두 실시하며 여러 분포/각도를 동시에
검사하는 경우 다중 비교를 고려한다. CPU/GPU 일치뿐 아니라 독립 기준과의
일치도 요구한다. 수광 표본이 부족하면 미검증으로 남기며 단순히 허용오차를
넓혀 통과시키지 않는다.

각 조건에서 preset/사용자 입력, 최종 적용 물성, source/trace 면 ID, 입사각,
base/effective 반사율, 입사/반사/수광/비반사/종료 광량, 분포 그래프, Ray 수,
seed, 실행 장치와 허용오차를 기록한다. 최종 보고에는 다음을 별도 표시한다.

1. 사용자 설정/기본값 전달 통과 여부.
2. 반사율 및 광속 보존 통과 여부.
3. 산란 분포의 정의 대비 통과 여부.
4. CPU/GPU 및 편집/ROI/저장 복원 회귀 통과 여부.
5. 실제 재질/실측 BSDF 적합성은 별도이며 아직 확인하지 않은 범위.

수정은 실패하는 회귀 테스트를 먼저 만들고 원인별로 나누어 진행한다. 광량을
맞추기 위한 임의 계수 조정이나 실패한 strict 기준 완화로 통과 처리하지 않는다.
고정 seed 반복은 재현성, 다중 seed는 통계 수렴, 이론값은 물리 모델의 제한된
정확도 검증으로 구분한다. 잔여 광량/에너지 ledger는 MIS/roulette 가중치를
고려해야 하며 상수 반사율 실험의 단순 `rho^depth` 공식을 일반화하지 않는다.

GPU 실험은 3개 실행 가이드와 production preflight/실행 증거 계약을 따른다.
새 실험마다 전달경로/commit, GPU, CUDA 성공/시도, CPU 보조/실패, 첫 실행과
warm 시간, emitter 종류를 남긴다. 기존 GPU 통과 기록으로 새 실험을 대신하지 않는다.

## 5. 실제 nit를 계산하려면

가상 휘도계 또는 방향/위치별 Receiver를 도입하고 관측 방향, 각도 수용 범위,
입사동공/측정 면적과 투영면적·입체각을 정의해야 한다. 저장된 소수의 표시용
Ray path만으로 휘도를 재구성하지 말고 전체 샘플의 가중 기여를 집계한다.
실측 보정 계수는 맞춤용 모델과 검증용 모델을 분리하여 전이 성능을 확인한다.
이 작업은 현재 lux 집계의 단순 이름 변경이 아니라 별도 측정 모델 개발이다.

## 참고

- [CIE 조도 정의](https://cie.co.at/eilvterm/17-21-060)
- [CIE 휘도 정의](https://cie.co.at/eilvterm/17-21-050)
- [CIE 광속 정의](https://cie.co.at/eilvterm/17-21-039)
- [PBRT Lambertian Reflection](https://www.pbr-book.org/3ed-2018/Reflection_Models/Lambertian_Reflection)

관련 코드: `src/leakage_simulator/raytrace_bridge.py`, `optics.py`,
`reflection.py`, `types.py::EmitterSpec.effective_power_lumen`,
`raytracer.py::_build_direct_metrics`,
`frontend/src/features/results/result-window.tsx`.
