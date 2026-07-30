# 고반사 Specular Surface Preset 추가

## 목적

- 고반사 경면 구조와 다회 반사 경로를 쉽게 시험할 수 있는 Surface property를 제공한다.
- 검정 소재 중심의 기존 preset만으로는 확인하기 어려운 높은 광량 보존 조건을 재현한다.

## 추가 Preset

| Surface property | 총 반사율 | Scatter model | Specular/Diffuse |
|---|---:|---|---:|
| Polished mirror · high reflectance | 0.85 | Specular | 1.0 / 0.0 |
| Enhanced mirror · very high | 0.95 | Specular | 1.0 / 0.0 |

- 두 값은 특정 실제 제품의 인증 물성이 아닌 V1 설계 비교용 reference 값이다.
- 실제 절대 밝기 예측에는 측정 데이터 또는 공급사 데이터를 사용해야 한다.

## 데이터 계약 보완

- 기존 Surface property는 `Base reflectance × Surface scale` 방식을 유지한다.
- 미러 코팅·고반사 필름처럼 표면 처리가 총 반사율을 지배하는 preset은 선택적 `reflectanceOverride`를 사용한다.
- 최종 compiled profile은 기존과 동일하게 0~1 범위의 `reflectance`만 Ray tracing backend에 전달한다.

## Material Library 검토

- 현재 남아 있는 catalog, Component Material popup, Part/Face assignment 기능과 빠져 있는 Library 관리 UI의 경계를 재정리했다.
- 별도 구현 계획은 `docs/material-library-react-reintroduction-plan.md`에 기록했다.

## 검증

- 기존 reflectance scale 방식 회귀 테스트
- R 0.85/R 0.95 절대 반사율 compile 확인
- 두 preset이 `specular_ratio=1`, `diffuse_ratio=0`, `scatter_model=specular`로 전달되는지 확인
- Frontend 전체 테스트, lint 및 production build 수행
