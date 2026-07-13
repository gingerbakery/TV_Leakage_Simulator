# Material Library UI 구조안

## 목적
- 사용자가 부품별/면별 광학 특성을 직관적으로 지정할 수 있게 한다.
- 복잡한 광학 용어를 최소한의 단계로 노출한다.

## 기본 원칙
- `base material`과 `surface finish`를 분리하되, UI에서는 자연스럽게 연결해서 보여준다.
- 왼쪽 패널은 라이브러리 관리 중심
- 오른쪽 3D viewer는 실제 대상 선택/하이라이트 중심
- 부품 할당은 transform popup과 유사하게 viewer 쪽 popup으로 연동 가능하게 한다.

## 좌측 메뉴 구조

### 1. Base materials
- 기본 재질 목록
- 예:
  - black powder aluminum
  - black pc resin
  - abs matte
  - white reference

기능:
- 목록 펼치기/접기
- 신규 material 등록
- reflectance 등 기본 값 수정

### 2. Surface properties
- 표면 산란/거칠기/코팅 특성
- 예:
  - lambertian
  - gaussian scatter
  - corrosion preset
  - matte finish

기능:
- 신규 surface property 등록
- 산란 파라미터 편집

### 3. BSDF assets
- 외부 측정 파일 목록
- 사용자가 업로드한 BSDF 자산 표시

기능:
- BSDF 파일 업로드
- 이름 관리
- 적용 가능 대상 표시

### 4. Saved optical profiles
- `base material + surface finish + optional bsdf` 조합 저장
- 자주 쓰는 조합을 빠르게 재사용

### 5. Assignments
- 현재 어떤 부품/면에 어떤 profile이 적용되었는지 요약

## 오른쪽 viewer 연계
- component에서 `Material` 버튼 선택
- 대상 부품 하이라이트
- popup에서 아래 입력 가능:
  - base material
  - surface finish
  - scatter model
  - optional BSDF
  - 적용 범위(component 전체 / 선택 face)

## 부품 단위 할당
- 기본 동작:
  - component 전체에 material 적용
- 필요한 경우:
  - 특정 face만 override

## face override
- 사용 예:
  - chassis 특정 절곡부만 다른 산란 특성
  - 부식/테이프/코팅이 일부 면에만 적용

## 신규 등록 UX
- `New material`
- `New surface property`
- `New BSDF`

버튼을 누르면:
1. 입력 폼 열기
2. 값 입력
3. `Save`
4. 목록에 등록

## UI 문구 원칙
- 전문 용어를 숨기지 않되 간단 설명을 붙인다.
- 예:
  - `Scatter model (빛이 퍼지는 방식)`
  - `Reflectance (반사율)`
  - `Assignment (현재 적용 대상)`

## 우선순위
1. component 전체 material 지정
2. surface property 지정
3. face override
4. BSDF 연결
5. saved profile 재사용

## 결론
- V1에서는 `직관적인 할당 흐름`이 핵심이다.
- 정밀한 optical DB보다, 사용자가 쉽게 선택하고 비교할 수 있는 구조를 우선한다.
