# 백엔드 데이터 계약 가이드

## 목적
- ROI, Gap, Ray trace, Engine 담당자가 병렬로 작업해도 충돌을 최소화한다.
- UI 변경과 백엔드 변경이 섞여 전체 구조가 불안정해지는 것을 방지한다.
- 각 모듈이 무엇을 입력받고 무엇을 반환하는지 명확히 고정한다.

## 적용 범위
- ROI 선택 및 receiver 해석
- Gap 생성 및 공차/이동/기울기 기반 모델
- Ray trace 및 밝기 누적 계산
- Engine 조합 및 결과 생성

## 핵심 원칙
- ROI는 `어디를 볼지`를 결정한다.
- Gap은 `어떻게 틈을 만들지`를 결정한다.
- Ray trace는 `빛이 어떻게 이동하고 얼마나 남는지`를 계산한다.
- Engine은 위 결과를 조합해 실행한다.
- UI는 사용자 입력을 계약 형식으로 변환해 전달만 한다.

## UI와 백엔드 분리 원칙
- ROI UI를 별도 시스템으로 완전히 분리할 필요는 없다.
- 대신 ROI 해석 규칙은 반드시 `src/leakage_simulator/roi.py`에 고정한다.
- `run_web.py`는 UI 상태를 관리하되, 최종 결과는 정해진 계약 형식으로만 넘긴다.

## 기능별 책임

### ROI 모듈
- 파일:
  - `src/leakage_simulator/roi.py`
  - `src/leakage_simulator/components.py`
- 책임:
  - mesh를 component/face 단위로 해석
  - ROI 대상 face 목록 생성
  - 기본 receiver 후보 구성
  - scene payload 생성
- 하지 말아야 할 것:
  - gap transmissive 계산
  - ray energy 감쇠 계산
  - material reflectance 계산

### Gap 모듈
- 파일:
  - `src/leakage_simulator/gap.py`
  - `src/leakage_simulator/types.py`의 `GapRule`
- 책임:
  - gap rule 정의
  - gap 샘플링
  - transmissive 계산
  - component move / local face move / bbox 기반 gap 해석
- 하지 말아야 할 것:
  - receiver 밝기 계산
  - ray hit 누적
  - UI 선택 상태 직접 관리

### Ray trace 모듈
- 파일:
  - `src/leakage_simulator/raytracer.py`
  - `src/leakage_simulator/types.py`의 실행/결과 타입
- 책임:
  - emitter 출사
  - 교차 판정
  - 반사/산란/감쇠
  - receiver irradiance 누적
  - `Nits_est` 계산
- 하지 말아야 할 것:
  - ROI 선택 방식 해석
  - component transform 규칙 생성
  - UI 상태 관리

### Engine 모듈
- 파일:
  - `src/leakage_simulator/engine.py`
- 책임:
  - import 결과 + ROI + gap + material + emitter + run config 조합
  - 실행 orchestration
  - JSON/CSV/PNG 결과 저장
- 하지 말아야 할 것:
  - ROI 내부 알고리즘 직접 확장
  - raytracer 물리 로직 직접 보유
  - gap 규칙 내부 구현 직접 보유

## 현재 권장 계약 구조

### ROI → Engine
- 필수 입력:
  - `roi_face_indices: List[int]`
- 추후 확장 가능:
  - `roi_component_ids: List[int]`
  - `roi_selection_mode: str`
  - `roi_bbox_min`, `roi_bbox_max`

권장 형태:

```python
@dataclass
class ROISelectionResult:
    face_indices: List[int]
    component_ids: List[int]
    selection_mode: str
    bbox_min: Optional[Vec3] = None
    bbox_max: Optional[Vec3] = None
```

### Engine → Gap
- 입력:
  - `gap_rules`
  - `face_count`
  - `rng`
- 출력:
  - `face_index -> GapSample`

### Engine → Ray trace
- 입력:
  - `mesh`
  - `emitters`
  - `gap_samples`
  - `receivers`
  - `materials`
  - `run_config`
- 출력:
  - `SimulationOutput`
  - `ReceiverMetrics`
  - 요약 summary

## GapRule 확장 방향
권장 필드:

```python
gap_mode: str
target_component_ids: List[int]
move_vector_mm: Optional[Vec3]
rotation_deg: Optional[Vec3]
```

예시 모드:
- `face_gap`
- `component_move_gap`
- `volume_gap`

## 개발 시 주의사항

### ROI 담당
- `roi.py` 반환 구조를 자주 바꾸지 않는다.
- 바꿀 경우 `engine.py`와 UI 변환 코드도 함께 수정한다.
- UI 편의 로직을 ROI 백엔드 계약에 섞지 않는다.

### Gap 담당
- gap 책임은 `gap size / transmissive`까지로 제한한다.
- component move나 bbox도 최종적으로 face 단위 결과로 환산한다.
- receiver 계산 책임을 가져가지 않는다.

### Ray trace 담당
- ROI 선택 방식 자체를 raytracer가 해석하지 않는다.
- geometry 이동 규칙을 raytracer 내부에 집어넣지 않는다.
- gap은 이미 계산된 face-level 입력으로 받는다고 가정한다.

### UI 담당
- hover, checkbox, selection dropdown 같은 것은 UI 전용 상태다.
- 백엔드에는 반드시 정해진 계약 형식으로 변환 후 전달한다.

## 변경 절차

### ROI 변경 시
1. `roi.py` 계약 영향 검토
2. `engine.py` 영향 검토
3. `run_web.py` 입력 변환 확인
4. `docs/changes/*.md` 기록 추가

### Gap 변경 시
1. `GapRule` 타입 영향 검토
2. `gap.py` 수정
3. `engine.py` 조합 확인
4. `raytracer.py` 입력 shape 확인
5. 문서 이력 추가

### Ray trace 변경 시
1. 입력 계약 확인
2. `raytracer.py` 계산 로직 수정
3. 결과 타입 영향 검토
4. 렌더/리포트 영향 검토
5. 문서 이력 추가

## 결론
- ROI가 Gap/Ray trace에 영향을 주는 것은 정상이다.
- 다만 그 영향은 반드시 `명시적인 데이터 계약`을 통해 전달되어야 한다.
- 가장 중요한 원칙은 `모듈이 서로 내부 구현을 몰라도 되게 유지하는 것`이다.
