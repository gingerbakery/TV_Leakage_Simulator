# 기구 도면 원점 = 시뮬레이터 절대좌표 보장 (회귀 테스트)

- 날짜: 2026-07-28
- 대상 브랜치: `main`

## 배경

사용자 요청 논의 중 "STEP import 시 좌표를 시뮬레이터 절대좌표에 맞춰 달라"는
요청이 있었으나, 확인 결과 실제 의도는 반대였다: **기구 도면(NX STEP) 자체의
(0, 0, 0)이 이미 Full View/ROI View에서 그대로 시뮬레이터의 절대좌표로 쓰이고
있고(변환 없음), 이 상태를 앞으로 다른 파일을 import하더라도 계속 보장해
달라**는 것이었다. 즉 코드 동작 변경이 아니라, 이 불변조건이 향후 실수로
깨지지 않도록 고정해 달라는 요청이다.

## 확인된 현재 동작 (변경 없음)

- `src/leakage_simulator/importers.py`의 모든 import 경로(`_import_step_ocp`,
  `_import_step`, `_import_obj`, `_import_stl_ascii`)는 좌표 재배치/정규화를
  전혀 하지 않는다. STEP 파일에 저장된 원점·축을 그대로 mesh 정점으로 사용한다.
- `src/leakage_simulator/roi.py::build_scene_payload()`도 이 mesh를 그대로
  사용하므로, API 응답의 component bbox·좌표도 CAD 원본 좌표계와 동일하다.
- 따라서 **기구 도면의 (0, 0, 0)이 시뮬레이터의 절대좌표(0, 0, 0)와 항상
  일치**하며, Full View의 world-origin 축 표시와 ROI View 모두 같은 좌표계를
  공유한다.

## 변경 사항

코드 동작 변경은 없음. 대신 이 보장을 고정하는 회귀 테스트를 추가했다:

- `tests/test_step_import_absolute_origin.py`
  - `test_import_geometry_does_not_translate_the_model`: 샘플 STEP
    (`samples/tv_leakage_full_assembled_no_gap.stp`)을 `STEPControl_Reader`로
    독립적으로 직접 읽어 얻은 bounding box(= "NX가 저장한 원본 좌표")와,
    `import_geometry()`가 만든 mesh의 bounding box를 비교해 일치함을 검증한다.
  - `test_scene_payload_component_bboxes_use_the_same_absolute_origin`:
    같은 검증을 `build_scene_payload()`의 API 응답 레벨(`components[].bbox_min/max`)
    에서도 반복해, Full View/ROI View가 실제로 소비하는 데이터까지 좌표가
    보존됨을 확인한다.
  - 이후 누군가 bounding-box 중심 정렬이나 원점 정규화 같은 좌표 변환을
    추가하면 이 테스트가 즉시 실패해 알려준다.

## 검증

- 신규 테스트 2개 포함, Python 회귀 테스트 88개 전체 통과.
