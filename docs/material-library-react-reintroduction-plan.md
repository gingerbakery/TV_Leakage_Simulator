# React Material Library 재도입 계획

## 현재 상태

Material 기능이 완전히 삭제된 것은 아니다.

- Built-in Base material과 Surface property catalog가 코드에 남아 있다.
- Component의 `Material` 편집창에서 Base material, Surface property 및 적용 범위를 선택할 수 있다.
- Part Assignment와 Face Override 데이터는 Ray tracing payload에 연결된다.
- `Applied Settings`에서 현재 assignment를 확인하고 삭제할 수 있다.

현재 빠진 핵심은 사용자가 catalog를 직접 조회·등록·수정·가져오기 할 수 있는 독립적인 **Material Library 관리 UI**다.

## 권장 UI 위치

- Material Library를 ROI·Components·Ray tracing 같은 작업 순서에 포함하지 않는다.
- 왼쪽 패널 하단의 Utility 영역에 `Material Library`를 독립 메뉴로 추가한다.
- 메뉴를 누르면 좌측의 좁은 accordion 안에 모든 편집창을 넣지 않고, 넓은 Library drawer 또는 dialog를 연다.
- Component별 실제 적용은 현재 3D Viewer의 Material popup을 계속 사용한다.

즉 역할을 다음처럼 분리한다.

| UI | 책임 |
|---|---|
| Material Library | 재료·표면·Profile·BSDF의 등록과 관리 |
| Component Material popup | 선택한 Part/Face에 Profile 적용 |
| Applied Settings | 현재 프로젝트에 적용된 Assignment 요약 |

## Library 화면 구조

### 1. Base Materials

- Built-in / Project Custom 구분
- 이름, 분류, 기본 총 반사율, 기본 Surface 표시
- `New`, `Duplicate`, `Edit`, `Archive`

### 2. Surface Properties

- Scatter model, 반사율 적용 방식, Specular/Diffuse 비율 표시
- 반사율 방식:
  - `Scale`: Base material 반사율에 배율 적용
  - `Absolute override`: 코팅·필름이 총 반사율을 지배할 때 사용
- `New surface property` 편집창에서 roughness와 Gaussian sigma 입력

### 3. Saved Optical Profiles

- Base material + Surface property + optional BSDF 조합
- 최종 Reflectance, Loss, Specular/Diffuse 및 Scatter model 미리보기
- 자주 쓰는 조합 즐겨찾기

### 4. BSDF Assets

- 파일명, 측정 장비, 입사각, 측정일, 버전, 단위 메타데이터 관리
- V1에서는 자산 등록과 Profile 연결까지만 수행
- 실제 BSDF 보간 계산은 V2 단계에서 활성화

### 5. Assignments

- Part Assignment와 Face Override를 표로 조회
- 대상 component/face, Profile, 우선순위, 활성 상태 표시
- 적용 편집은 3D Viewer popup으로 연결

## 데이터 저장 원칙

- Built-in catalog는 읽기 전용으로 유지한다.
- 사용자가 만든 항목은 Project Custom catalog에 저장한다.
- Custom material, surface, profile 및 BSDF metadata를 `.bitsam` 파일에 포함한다.
- 같은 ID 충돌을 방지하기 위해 사용자 항목은 UUID 기반 ID를 사용한다.
- Built-in preset을 수정할 때는 원본을 바꾸지 않고 `Duplicate as custom`을 사용한다.
- schema version과 migration 함수를 추가해 과거 `.bitsam` 파일을 계속 불러올 수 있게 한다.

## 단계별 권장 구현

### ML-0. 데이터 계약 보완

- Base/Surface/Profile/BSDF schema 확정
- `scale`과 `absolute override` 의미 분리
- `.bitsam` custom catalog migration 정의

### ML-1. Library 조회·등록

- 독립 Material Library dialog
- Built-in 목록 조회
- Custom material/surface/profile 생성·수정·복제
- `.bitsam` 저장·불러오기

### ML-2. Assignment 연동 강화

- Library Profile을 Component popup에서 즉시 선택
- Part/Face assignment 요약 및 충돌 경고
- 미지정 surface 및 override 우선순위 표시

### ML-3. 측정 데이터 Import

- CSV/JSON 기반 반사율 데이터 import
- BSDF asset metadata 및 파일 연결
- 단위·입사각·파장 범위 validation

### ML-4. 사내 표준화

- 측정 출처, 승인 상태, 버전, 담당자 관리
- 공용 catalog 동기화
- 이 단계에서만 Supabase 또는 사내 DB 도입 검토

## 추천 결론

- 지금은 full Material Library를 급히 넣기보다 Ray tracing 결과와 Surface assignment 흐름을 안정화한다.
- 다음 구현은 `ML-0 → ML-1`까지만 진행하는 것이 적절하다.
- 초기 저장소는 `.bitsam` 프로젝트 내부 JSON으로 충분하다.
- 사내 공용 DB는 여러 개발자가 동일 catalog를 공유하고 승인·버전 관리가 필요해지는 시점에 도입한다.
