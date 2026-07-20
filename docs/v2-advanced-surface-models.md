# V2 고급 표면 반사·산란 모델 검토

## 목적
- V1의 `Specular + Gaussian + Lambertian` 혼합 모델로 표현하기 어려운 실제 표면 거동을 정리한다.
- V2 진입 시 어떤 모델을 추가할지 판단할 수 있도록 적용 대상, 필요 입력값, 검증 방법을 고정한다.
- 기능을 한꺼번에 추가하지 않고 실측 오차와 설계 의사결정 영향이 큰 항목부터 선택적으로 적용한다.

## V1 기준 모델

V1은 다음 세 반사 lobe를 혼합하여 표면을 근사한다.

```text
총 반사광
= Specular 성분
+ Gaussian glossy 성분
+ Lambertian diffuse 성분
```

- `Specular`: 완전 경면 반사
- `Gaussian`: 정반사 방향 주변의 원형 산란 분포
- `Lambertian`: 법선 반구의 이상적인 cosine-weighted 확산반사

이 구조는 경면, 반광, 무광 표면의 상대 비교에 적합하지만 다음 네 경우에는 한계가 있다.

---

## Case 1. 거친 확산면의 방향 의존성

### 현상
- 실제 거친 무광면은 Lambertian처럼 모든 관측 방향에서 동일한 밝기로 보이지 않는다.
- 입사 방향과 관측 방향의 관계에 따라 후방산란이 증가할 수 있다.
- 표면 미세 요철에 의한 masking, shadowing, 내부 상호반사가 발생한다.

### TV 적용 예
- 강한 부식이 적용된 PC/ABS
- 거친 검정 분체도장
- 샌드블라스트 또는 에칭 처리 금속
- 다공성 흡광재 표면

### V1 한계
- Lambertian은 이상적인 완전 확산면이므로 거칠기에 따른 방향성 변화를 표현하지 못한다.
- Gaussian은 정반사 방향 주변 lobe이므로 거친 diffuse 성분의 후방산란을 대신하기 어렵다.

### V2 후보 모델
#### Oren–Nayar
- Lambertian의 거친 표면 확장 모델이다.
- 표면 slope 분포를 나타내는 roughness angle `sigma`를 사용한다.
- 입사 방향과 출사 방향 관계에 따른 거친 diffuse 반사를 계산한다.

#### Energy-preserving Oren–Nayar
- Oren–Nayar 계열에서 발생할 수 있는 에너지 손실/증가 문제를 보정한 모델이다.
- 절대 밝기 비교 정확도가 중요해질 경우 우선 검토한다.

### 추가 입력값
- diffuse roughness angle `sigma_deg`
- diffuse reflectance
- 선택적으로 표면 처리 preset과 `sigma_deg` 매핑

### 권장 검증
- 동일 반사율에서 Lambertian과 Oren–Nayar의 각도별 Receiver 분포 비교
- 부식 사양별 실측 배광 또는 BSDF와 비교
- 에너지 적분값이 총 diffuse reflectance와 일치하는지 확인

---

## Case 2. 입사각에 따른 반사율과 광택 변화

### 현상
- 실제 레진과 도장면은 광선이 표면을 비스듬하게 스칠수록 반사가 강해질 수 있다.
- 표면 거칠기에 따라 정반사 peak의 폭과 높이가 동시에 변한다.
- 단순 고정 반사율과 Gaussian 폭만으로는 grazing angle 거동을 정확히 표현하기 어렵다.

### TV 적용 예
- 경면 또는 반광 PC
- 도장된 금속 샤시
- 광택 테이프
- gap 내부의 얕은 입사각 다중 반사 경로

### V1 한계
- `reflectance`가 입사각과 무관한 상수다.
- Gaussian lobe가 Fresnel 증가와 미세면 shadowing/masking을 표현하지 않는다.

### V2 후보 모델
#### Fresnel–Schlick
- 입사각에 따른 반사율 변화를 비교적 가볍게 계산한다.
- 굴절 ray를 만들지 않더라도 반사율의 각도 의존성 계산에 사용할 수 있다.

#### Microfacet BRDF
- 표면을 작은 미세 반사면의 집합으로 모델링한다.
- 일반 구성:
  - normal distribution function
  - Fresnel term
  - geometry masking-shadowing term

#### GGX 또는 Beckmann
- `GGX`: 긴 반사 tail을 표현하며 거친 glossy 표면에 자주 사용된다.
- `Beckmann`: 비교적 Gaussian slope에 가까운 미세면 분포다.

### 추가 입력값
- roughness 또는 alpha
- 정상 입사 반사율 `F0` 또는 굴절률 기반 입력
- microfacet distribution 종류

### 권장 검증
- 입사각 sweep에 따른 총 반사율 비교
- roughness 변화에 따른 lobe 폭과 peak 변화 확인
- 현재 Gaussian 모델과 결과/속도 비교
- 고정 반사율 대비 실제 빛샘 설계 우열이 바뀌는지 확인

---

## Case 3. 방향성이 있는 표면

### 현상
- 표면 결 방향에 따라 산란 분포가 원형이 아니라 타원형으로 나타난다.
- 한 축 방향으로는 좁고 다른 축 방향으로는 넓게 퍼질 수 있다.

### TV 적용 예
- 브러시드 또는 헤어라인 금속
- 사출 flow mark
- 방향성 연마면
- 압연 또는 가공 방향이 남은 금속면
- 방향성 테이프/필름 표면

### V1 한계
- 현재 Gaussian은 모든 방위각 방향으로 동일한 isotropic 분포다.
- 표면 tangent 방향 정보가 optical profile에 없다.

### V2 후보 모델
#### Anisotropic Gaussian
- tangent/bitangent 축마다 서로 다른 Gaussian 폭을 사용한다.
- 기존 Gaussian 구현을 비교적 단순하게 확장할 수 있다.

#### Anisotropic GGX
- 미세면 roughness를 두 축으로 분리한다.
- grazing angle과 방향성 glossy 반사를 함께 표현할 수 있다.

### 추가 입력값
- surface tangent direction
- `sigma_u_deg`, `sigma_v_deg` 또는 `roughness_u`, `roughness_v`
- CAD 좌표 기준 방향 또는 face local 방향

### 권장 검증
- 동일 표면을 90도 회전했을 때 Receiver 분포도 함께 회전하는지 확인
- 두 축의 분포 폭이 입력값과 일치하는지 확인
- CAD face tangent 데이터의 일관성 확인

---

## Case 4. 역반사·후방산란 및 비대칭 lobe

### 현상
- 일부 요철, 코팅 또는 미세 구조는 광을 정반사 방향이 아니라 입사 방향 쪽으로 되돌려 보낼 수 있다.
- 산란 분포가 대칭적인 Gaussian 또는 Lambertian 형태가 아닐 수 있다.
- 다중 peak 또는 비대칭 tail이 나타날 수 있다.

### TV 적용 예
- 특정 엠보싱 또는 요철 패턴
- 다공성 흑색 코팅
- 특수 흡광 테이프
- 미세 구조를 가진 사출/도장면
- 측정 결과에서 비대칭 lobe가 확인된 표면

### V1 한계
- Specular, Gaussian, Lambertian 모두 단순하고 대칭적인 해석 모델이다.
- 실제 측정 분포의 다중 peak와 비대칭 특성을 직접 표현하지 못한다.

### V2 후보 모델
#### Retroreflective lobe
- 입사 방향 반대축 주변에 별도 lobe를 추가한다.
- `retro_ratio`, `retro_sigma_deg`로 단순 근사할 수 있다.

#### Tabulated BSDF/BRDF
- 측정된 입사각/출사각별 분포를 테이블 형태로 읽는다.
- 복잡하거나 비대칭적인 표면의 최종 기준 모델로 사용한다.

#### Analytic lobe mixture
- Specular, diffuse, retroreflective lobe를 여러 개 혼합한다.
- BSDF 데이터가 부족한 초기 단계의 근사 모델로 사용할 수 있다.

### 추가 입력값
- retroreflection 비율과 폭
- 측정 BSDF 파일
- 측정 좌표계, 각도 convention, normalization 정보

### 권장 검증
- 입사 방향을 변경했을 때 후방 peak가 함께 이동하는지 확인
- BSDF 적분값과 총 반사율의 일치 여부 확인
- interpolation 전후 에너지 보존 확인
- 측정 데이터가 없는 각도에서 extrapolation 경고 제공

---

## V2 후보 우선순위

### Priority 1: Fresnel + Microfacet GGX
- gap 내부에서는 grazing incidence가 자주 발생할 가능성이 높다.
- 경면/반광 레진과 도장면의 반사 방향 및 세기 정확도 개선 효과가 클 수 있다.

### Priority 2: Oren–Nayar
- 강한 부식과 거친 분체도장의 diffuse 방향성을 개선한다.
- Lambertian 대비 실측 정합 효과를 먼저 확인한다.

### Priority 3: 측정 BSDF
- 사내 측정 데이터 형식이 확보된 표면부터 적용한다.
- 비대칭·다중 lobe 표면의 기준 모델로 사용한다.

### Priority 4: Anisotropic 및 Retroreflective 모델
- 실제 TV 소재에서 방향성 또는 역반사 영향이 확인될 때 선택적으로 적용한다.

## V2 진입 판단 기준

다음 중 하나 이상이 발생하면 고급 표면 모델 검토를 시작한다.

- V1 모델과 실측 Receiver 밝기/분포의 차이가 설계 우열을 바꾼다.
- 입사각 변화에 따른 실측 반사율 오차가 허용 범위를 넘는다.
- 부식 사양별 실측 분포를 Lambertian/Gaussian 조합으로 맞추기 어렵다.
- 방향성 또는 비대칭 BSDF가 확인된다.
- 상대 밝기뿐 아니라 절대 nit 정합 목표가 강화된다.
- V1 ray tracing 성능과 회귀 테스트가 안정화되어 모델 고도화를 수용할 수 있다.

## V2 진입 체크리스트

- [ ] V1 Specular/Lambertian/Gaussian 회귀 테스트 완료
- [ ] 대표 PC, 분체도장, 테이프 표면 실측 데이터 확보
- [ ] 입사각별 반사율 데이터 확보 여부 확인
- [ ] 부식 표준 사양과 roughness/BSDF 매핑 검토
- [ ] Oren–Nayar 필요성 평가
- [ ] Fresnel + GGX/Beckmann 필요성 평가
- [ ] Anisotropic surface 필요성 평가
- [ ] Retroreflective/non-symmetric lobe 필요성 평가
- [ ] BSDF 파일 포맷과 좌표계 계약 확정
- [ ] 계산 정확도 향상 대비 성능 비용 측정

## 리마인드

**V2 phase를 시작할 때 이 문서를 반드시 다시 검토한다.**

특히 다음 네 항목을 구현 후보에서 누락하지 않는다.

1. 거친 diffuse 표면: Oren–Nayar 계열
2. 입사각/미세면 효과: Fresnel + Microfacet GGX/Beckmann
3. 방향성 표면: Anisotropic Gaussian/GGX
4. 역반사·비대칭 표면: Retroreflective lobe 또는 측정 BSDF
