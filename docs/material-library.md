# Material Library 데이터 구조안

## 목적
- 재료 자체의 반사율과 표면의 반사·산란 거동을 분리해 관리한다.
- TV 기구에서 자주 쓰는 검정 금속, 검정 레진 등의 기본 프리셋을 제공한다.
- Part Assignment와 Face Override를 모두 지원한다.
- 향후 측정 BSDF 파일을 연결할 수 있게 확장 지점을 유지한다.

## V1 광학 범위
- 계산 대상: 반사율, 정반사, 확산/산란.
- 제외 대상: 투과, 굴절, 회절, 간섭, 파장별 색도.
- 반사되지 않은 광량은 surface에서 종료된 것으로 처리한다.
- 별도의 흡수율을 사용자가 입력하지 않는다. 에너지 보존을 위해 내부적으로 `loss = 1 - reflectance`를 사용한다.

## 데이터 계층

### Base material
재료의 기본 총 반사율을 정의한다.

권장 필드:
- `material_id`
- `display_name`
- `reflectance_total`
- `default_surface_id`
- `notes`

예시:

```json
{
  "material_id": "black_pc_resin",
  "display_name": "Black PC Resin",
  "reflectance_total": 0.08,
  "default_surface_id": "matte_resin",
  "notes": "TV bezel/resin reference"
}
```

### Surface property
표면 가공 상태가 반사율과 방향 분포에 미치는 영향을 정의한다.

권장 필드:
- `surface_id`
- `display_name`
- `reflectance_scale`
- `scatter_model`
- `specular_ratio`
- `diffuse_ratio`
- `roughness`
- `gaussian_sigma_deg`
- `bsdf_asset_id`
- `notes`

예시:

```json
{
  "surface_id": "powder_coarse",
  "display_name": "Black powder, coarse",
  "reflectance_scale": 1.33,
  "scatter_model": "mixed",
  "specular_ratio": 0.05,
  "diffuse_ratio": 0.95,
  "roughness": 0.85,
  "gaussian_sigma_deg": 24.0,
  "bsdf_asset_id": null
}
```

### Optical profile
Ray tracing에 실제로 전달되는 최종 광학 속성이다.

```text
reflectance = clamp(base.reflectance_total * surface.reflectance_scale, 0, 1)
loss = 1 - reflectance
specular_ratio + diffuse_ratio = 1
```

예시:

```json
{
  "profile_id": "black_pc_resin__matte_resin",
  "reflectance": 0.08,
  "specular_ratio": 0.10,
  "diffuse_ratio": 0.90,
  "scatter_model": "mixed",
  "roughness": 0.65,
  "gaussian_sigma_deg": 18.0
}
```

`absorption` 필드가 이전 데이터에 포함되어도 backend는 이를 독립 물성으로 사용하지 않고 `1 - reflectance`로 다시 계산한다.

## Scatter model
- `none`: 반사 ray를 생성하지 않는다.
- `specular`: 법선 기준 정반사 방향을 사용한다.
- `lambertian`: 법선 반구에서 cosine-weighted 분포를 사용한다.
- `gaussian`: 지정 축 주변 Gaussian lobe를 사용한다.
- `mixed`: `specular_ratio`와 `diffuse_ratio`에 따라 정반사와 산란을 혼합한다.

RT-2C 방향 sampling:
- `specular`: 이상적인 정반사 방향 하나를 사용한다.
- `gaussian`: 이상적인 정반사 방향 주변의 Gaussian lobe를 사용한다.
- `lambertian`: surface normal 반구의 cosine-weighted 분포를 사용한다.
- `mixed`: `specular_ratio` 성분은 Gaussian glossy lobe, `diffuse_ratio` 성분은 Lambertian lobe로 sampling한다.

`mixed`에서 완전 정반사 성분이 반드시 필요한 표면은 별도 `specular` profile 또는 향후 3-lobe profile 확장을 사용한다.

## Assignment 구조

### Part Assignment
component 전체에 하나의 profile을 적용한다.

```json
{
  "assignment_id": "assignment_part_12",
  "target_type": "part",
  "component_id": 12,
  "profile_id": "black_pc_resin__matte_resin",
  "priority": 0,
  "enabled": true
}
```

### Face Override
component 중 특정 face 집합에 다른 profile을 적용한다.

```json
{
  "assignment_id": "assignment_faces_12_edge",
  "target_type": "faces",
  "component_id": 12,
  "face_indices": [101, 102, 103],
  "profile_id": "black_pc_resin__polished_edge",
  "priority": 10,
  "enabled": true
}
```

## 조회 우선순위
1. Face Override
2. Part Assignment
3. CAD mesh의 material ID와 같은 profile
4. `default` profile
5. 미지정 안전 profile: 반사율 0, ray 종료

같은 대상에 assignment가 여러 개 있으면 `priority`가 높은 항목을 사용한다. priority가 같으면 나중에 전달된 항목을 사용한다.

## 사용자 정의와 BSDF
- 사용자 정의 material은 이름과 총 반사율을 저장한다.
- 사용자 정의 surface는 반사율 배율, scatter model, 정반사/확산 비율을 저장한다.
- BSDF 파일은 surface property에 연결하되, 측정 데이터 보간 엔진은 후속 단계에서 구현한다.
- BSDF가 없는 V1에서는 preset 또는 사용자 입력 파라미터를 사용한다.

## 검증 원칙
- 모든 반사율은 0~1 범위로 제한한다.
- `specular_ratio + diffuse_ratio`는 항상 1로 정규화한다.
- `incoming_flux = reflected_flux + terminated_flux`가 성립해야 한다.
- 미지정 surface 수와 광량을 결과에 경고 지표로 표시한다.
- 합성 테스트에서는 Face Override, Part Assignment, CAD material fallback 순서를 각각 검증한다.

## 향후 확장
- 측정 BSDF/BRDF 보간
- 입사각 의존 반사율
- profile 버전 관리
- 사내 표준 preset 및 측정 이력 연결
- 분광/시감도 모델은 별도 백로그로 유지
