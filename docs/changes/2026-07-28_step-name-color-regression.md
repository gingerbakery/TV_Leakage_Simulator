# STEP 부품명·색상 통합 회귀 검증

## 목적

- CAD import 진단 기능과 API 포트 변경을 최신 `main`에 통합한 뒤에도 다른 개발자가 추가한 STEP 제품구조 부품명·색상 처리가 유지되는지 검증한다.

## 검증 범위

- 샘플 STEP 3종을 OCP/XCAF 경로로 직접 import
- mesh face metadata의 `step_component_name`, `step_component_color` 확인
- API scene payload의 component 이름·색상·face 수 일치 확인
- import 단계별 진단 시간에 `ocp_product_structure`가 포함되는지 확인

## 결과

- 전체 TV 및 좌·우 ROI STEP 모두 4개 부품을 정상 인식했다.
- `Chassis_Rear`, `LCD_Cell_3T`, `Frame_Middle_FMB`, `Cover_Deco` 이름이 유지됐다.
- 각 부품의 STEP 색상값이 mesh metadata와 API scene payload에서 동일하게 유지됐다.
- STEP 이름·색상과 CAD import 진단 계측 사이의 기능 충돌은 발견되지 않았다.

## 자동 회귀 테스트

- `tests/test_step_import_absolute_origin.py`에 STEP 제품구조 이름·색상 보존 테스트를 추가했다.
