# Receiver normal_flip 실제 반영 + Acceptance 라벨 축약

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## `normal_flip`이 Receiver 판정에 반영되지 않던 문제 수정

조사 결과 "Flip receiving normal" 체크박스는 Emitter에는 실제로
적용되지만(`raytracer.py`의 `_sample_*_emitter_ray`), Receiver는
`_build_receiver_frame()`이 `receiver.normal_flip`을 전혀 읽지 않아서
hit-test(`_find_first_receiver_hit`)가 항상 원본(반전 안 된) normal을
썼다. 3D 뷰어의 배치 미리보기(`createPlacementPlane`)는 이미 화면
표시용 화살표를 반전시키고 있어서, 체크박스가 "작동하는 것처럼
보이지만 실제 계산에는 반영 안 됨"이라는 불일치가 있었다.

- `src/leakage_simulator/raytracer.py`
  - `ReceiverFrame`에 `normal: Vec3` 필드 추가.
  - `_build_receiver_frame()`이 `receiver.normal_flip`이 참이면 저장할
    normal을 반전시켜 `ReceiverFrame.normal`에 담는다(Emitter의 기존
    `if emitter.normal_flip: normal = vec_mul(normal, -1.0)` 패턴과
    동일).
  - `_find_first_receiver_hit()`이 평면 교차·입사각 판정에 쓰는 normal을
    `receiver.normal`(원본) 대신 `frame.normal`(반전 반영됨)로 변경.
  - `ReceiverHitCandidate.normal`(리포팅/시각화용)도 `frame.normal`을
    쓰도록 통일 - 실제 판정에 쓰인 normal과 결과에 기록되는 normal이
    어긋나지 않게.
- `tests/test_raytracer_rt1.py`에
  `test_receiver_normal_flip_reverses_which_side_receives` 추가:
  일부러 "틀린" 방향의 normal에 `normal_flip=True`를 줘서, 반전 없이는
  히트가 0개였을 상황이 반전 덕분에 다시 히트를 받는지 확인한다(수정
  전이었다면 이 테스트가 실패했을 것).

## UI: "Acceptance angle (deg)" → "Acceptance (deg)"

`ray-tracing-panel.tsx`의 Datum plane receiver 다이얼로그에서
Resolution X/Y와 같은 행에 배치하면서 라벨이 두 줄로 밀리던 문제.
화면 라벨만 "Acceptance (deg)"로 줄이고, 접근성 이름(`aria-label`)은
기존 "Acceptance angle (deg)"를 그대로 유지했다.

## 검증

- 백엔드: 신규 회귀 테스트 포함 93개 통과.
- 프론트: `tsc -b`, `vitest run` 18 files / 94 tests 통과.
- 실제 브라우저로 Datum plane receiver 다이얼로그 레이아웃 재확인.
