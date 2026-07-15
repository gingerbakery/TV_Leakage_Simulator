# CAD식 Emitter 설정 UI

## 목적

- 숫자로 face index와 normal을 직접 입력하던 기존 광원 설정을 CAD 선택 방식으로 변경한다.
- 사용자가 3D viewer에서 실제 방출 surface를 확인하면서 emitter를 생성하고 편집할 수 있게 한다.
- RT-0의 `EmitterSpec` 데이터 계약과 UI 입력을 같은 형식으로 연결한다.

## Web UI 버전

- `v0.7.16`

## 구현 내용

### Emitter 생성 흐름

1. 왼쪽 `Ray tracing > Emitters`에서 `Add face emitter`를 누른다.
2. 3D viewer에서 광 방출 면을 클릭한다.
3. `Ctrl + 클릭`으로 연결 surface를 추가하거나 제외한다.
4. 3D viewer의 `Emitter properties` 팝업에서 광원 조건을 입력한다.
5. `Apply`를 눌러 emitter를 등록한다.

### Emitter 입력 항목

- 이름
- 광속 `Power (lm)`
- emitter별 ray 수
- 방향 분포
  - Lambertian 기본
  - Isotropic
  - Gaussian
- Gaussian sigma
- 선택 면 normal 반전
- 선택 face 집합

### 3D 표시

- 선택 중인 emitter surface는 노란색으로 표시한다.
- 등록된 emitter surface는 주황색으로 유지한다.
- 방출 normal 방향은 노란색 화살표로 표시한다.
- 부품 transform이 적용된 경우 emitter surface와 normal 표시도 적용 위치를 따라간다.
- Full CAD View와 ROI View에 동일한 emitter overlay를 표시한다.

### Emitter 관리

- 왼쪽 emitter 목록에서 등록된 광원의 이름, face 수, 방향 분포, lumen을 확인한다.
- 목록을 클릭하면 해당 emitter를 다시 편집한다.
- `Select faces`로 방출 면을 재선택한다.
- `Reset`으로 편집 입력값을 저장값 또는 기본값으로 되돌린다.
- `Delete`로 선택 emitter를 삭제한다.

## 데이터 계약 연결

- UI emitter 목록은 hidden field `emitter_specs_json`에 직렬화한다.
- 직렬화 형식은 `src/leakage_simulator/types.py`의 `EmitterSpec` 필드와 일치한다.
- 주요 필드는 아래와 같다.
  - `emitter_id`
  - `emitter_type=face`
  - `face_indices`
  - `normal_mode=face_normal`
  - `normal_flip`
  - `direction_distribution`
  - `gaussian_sigma_deg`
  - `power_lumen`
  - `ray_count`
  - `enabled`
- 기존 V0/V1 실행 흐름 호환을 위해 첫 번째 활성 emitter는 legacy form field에도 동기화한다.

## 검증

- Python 문법 검사 통과
- 렌더링된 module/classic JavaScript의 `node --check` 통과
- RT-1 단위 테스트 2건 통과
- 브라우저 수동 검증
  - Add face emitter 진입
  - 3D surface 선택 및 연결 면 2개 선택
  - 자동 normal과 면적 표시
  - normal flip
  - Apply 후 emitter 목록 등록
  - `EmitterSpec` JSON 직렬화
  - Full/ROI emitter overlay 표시
  - 브라우저 console error 없음

## 현재 범위와 다음 단계

- 이번 버전은 RT-1의 기본인 `face emitter`에 집중한다.
- 체적/허공 광원은 삭제하지 않고 후속 CAD식 배치 기능으로 보류한다.
- 다음 단계는 같은 방식으로 receiver를 3D viewer에 배치한 뒤 RT-1 direct ray tracing 입력에 연결하는 것이다.
