# RT-2B Optical Property Lookup 구현

## 변경 목적
- CAD surface 최초 충돌 시 적용할 반사율과 산란 속성을 결정한다.
- Part Assignment와 Face Override가 실제 ray trace backend까지 전달되게 한다.
- RT-2C 반사 방향 생성 전에 에너지 예산과 우선순위를 고정한다.

## 구현 내용
- `OpticalAssignment` 데이터 계약 추가.
- `OpticalPropertyResolver` 추가.
- 조회 우선순위 고정:
  1. Face Override
  2. Part Assignment
  3. CAD mesh material ID
  4. default profile
  5. unassigned 안전 종료 profile
- Surface `RayHit`에 profile ID, 반사율, scatter model, assignment source 기록.
- `outgoing_energy_lumen = incoming_energy_lumen * reflectance` 적용.
- profile별 hit 수, 입사 광량, 반사 가능 광량을 `_optical_summary`에 집계.
- Web UI를 v0.9.7로 갱신하고 Material Library 입력을 backend profile/assignment payload로 연결.
- Result에 resolved/unassigned hit 및 profile별 반사 가능 광량 표시.

## 광학 처리 원칙
- V1에서는 투과 ray를 생성하지 않는다.
- 반사되지 않은 광량은 surface에서 종료한다.
- 독립 흡수율 입력은 사용하지 않고 내부 loss를 `1 - reflectance`로 계산한다.
- 정반사/확산 비율은 합이 1이 되도록 정규화한다.
- RT-2B는 반사 에너지 예산만 계산한다. 실제 정반사, Lambertian, Gaussian 방향 생성은 RT-2C 범위다.

## UI 데이터 조합
```text
최종 총 반사율 = clamp(Base material 총 반사율 × Surface reflectance multiplier, 0, 1)
```

- Base material은 재료의 총 반사율을 소유한다.
- Surface property는 반사율 배율, scatter model, 정반사/확산 비율을 소유한다.
- 기존 독립 absorption UI는 제거했다.

## 검증 시나리오
- Mesh material profile: R=0.05, 1 lumen 입사 → 0.05 lumen 반사 가능.
- Part Assignment: R=0.20, 1 lumen 입사 → 0.20 lumen 반사 가능.
- Face Override: R=0.80, 1 lumen 입사 → 0.80 lumen 반사 가능.
- 각 시나리오 10,000 rays, optical property 미지정 hit 0건.
- R=0.80은 우선순위 검증용 합성 profile이며 TV 기본 소재값이 아니다.

## 산출물
- `src/leakage_simulator/optics.py`
- `tests/test_optics_rt2b.py`
- `scripts/generate_rt2b_optical_lookup_report.py`
- `outputs/rt2b_optical_lookup_report/rt2b_optical_lookup_report.png`
- `outputs/rt2b_optical_lookup_report/summary.json`
- `docs/optical-property-data-contract.md`

## 다음 단계
- RT-2C에서 법선 방향 정합, 정반사 벡터, Lambertian cosine-weighted sampling, Gaussian lobe, mixed sampling을 구현한다.
- RT-2C는 RT-2B가 계산한 반사 가능 광량을 그대로 사용하며 reflectance를 중복 적용하지 않는다.
