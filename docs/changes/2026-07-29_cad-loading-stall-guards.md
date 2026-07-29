# CAD 로딩 장시간 정지 보호장치

## 문제

- 회사 PC에서 CAD Import 후 `Loading CAD scene`이 10분 이상 지속되는 현상이 보고되었다.
- 최신 코드의 로컬 기준 측정에서는 동일 TV 샘플 STEP이 약 2.6초, 기어 STEP이 약 2.5초에 완료되었다.
- 회사 환경의 OCP DLL 보안 검사와 대형 CAD의 전역 ROI mesh 세분화·중복 scene 요청을 주요 병목으로 분류했다.

## 변경

- 모든 CAD 장기 단계 시작 시 `[CAD] ... START` 로그 출력
- 동일 CAD에 대한 동시 scene 요청 병합
- 최근 scene payload 메모리 캐시
- 50,000 faces 이상의 native mesh는 전역 ROI 세분화 자동 생략
- `LEAKAGE_CAD_FORCE_ROI_SUBDIVISION` 강제 세분화 옵션 추가
- `LEAKAGE_CAD_SKIP_PRODUCT_METADATA` 이름·색상 해석 제외 진단 옵션 추가
- `check_cad_import.py --skip-product-metadata` 추가
- Viewer CAD 로딩 경과시간과 30초 이상 진단 안내 추가

## 정확도 영향

- 자동 생략되는 것은 평면 ROI 선택을 위해 추가하던 삼각형 세분화다.
- OCP가 생성한 native CAD Tessellation 형상 자체는 유지한다.
- 따라서 Ray tracing의 기본 CAD 표면 형상은 유지되며, 이미 조밀한 대형 mesh의 불필요한 face 증가만 방지한다.
- 이름·색상 제외 모드는 사용자가 환경 변수로 명시한 진단 상황에서만 동작한다.
