# Material Library 데이터 구조안

## 목적
- 재질(material)과 표면 특성(surface optical behavior)을 분리해 관리한다.
- TV 기구에서 자주 쓰는 소재를 기본 프리셋으로 제공한다.
- 추후 사용자 정의 material, surface, BSDF를 확장 가능하게 한다.

## 기본 개념
- `material`
  - 재료 자체의 반사율/기본 특성
- `surface finish`
  - 표면 산란, 거칠기, 코팅, 부식 상태
- `bsdf asset`
  - 측정 기반 외부 파일
- `optical profile`
  - material + surface + optional bsdf 조합

## 기본 프리셋 예시

### Base material
- `black_powder_aluminum`
- `black_pc_resin`
- `matte_abs`
- `white_reference`

권장 속성:
- `material_id`
- `display_name`
- `reflectance`
- `absorption`
- `notes`

예시:

```json
{
  "material_id": "black_pc_resin",
  "display_name": "Black PC Resin",
  "reflectance": 0.08,
  "absorption": 0.92,
  "notes": "TV bezel/resin reference"
}
```

### Surface finish
- `lambertian_matte`
- `gaussian_soft`
- `corrosion_light`
- `corrosion_heavy`

권장 속성:
- `surface_id`
- `scatter_model`
- `roughness`
- `sigma_deg`
- `notes`

예시:

```json
{
  "surface_id": "gaussian_soft",
  "scatter_model": "gaussian",
  "roughness": 0.35,
  "sigma_deg": 18.0
}
```

### BSDF asset
권장 속성:
- `bsdf_id`
- `display_name`
- `file_path`
- `source`
- `notes`

## Optical profile
- 목적:
  - 실제 적용에 쓰는 단위
- 예시:

```json
{
  "profile_id": "tv_black_chassis_default",
  "base_material_id": "black_powder_aluminum",
  "surface_id": "lambertian_matte",
  "bsdf_id": null
}
```

## Assignment 구조

### Component assignment
- component 전체에 적용

```json
{
  "target_type": "component",
  "target_component_id": 12,
  "profile_id": "tv_black_chassis_default"
}
```

### Face override
- 특정 face 집합에 override 적용

```json
{
  "target_type": "face_override",
  "target_component_id": 12,
  "face_indices": [101, 102, 103],
  "profile_id": "corroded_edge_profile"
}
```

## 사용자 정의 material
- 사용자가 이름과 반사율을 직접 입력해 저장
- 내부적으로는 base material과 동등한 구조로 관리 가능

예시:

```json
{
  "material_id": "user_black_test_01",
  "display_name": "User Black Test 01",
  "reflectance": 0.05,
  "absorption": 0.95
}
```

## V1에서 필요한 최소 필드
- base material:
  - `material_id`
  - `display_name`
  - `reflectance`
- surface finish:
  - `surface_id`
  - `scatter_model`
  - `roughness`
- assignment:
  - `target_type`
  - `target_component_id`
  - `profile_id`

## 향후 확장
- wavelength band별 reflectance
- measured BSDF interpolation
- profile versioning
- company standard preset library

## 결론
- 이 프로젝트에서 실제 의사결정에 중요한 것은 `재질 이름`보다 `표면 광학 거동`이다.
- 따라서 V1은 `base material + surface finish + assignment` 구조를 우선 고정하는 것이 적절하다.
