# Datum plane receiver 다이얼로그 단순화

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 변경 사항

`ray-tracing-panel.tsx`의 `ReceiverDialog` (`datum_plane` 모드):

- 필드 그룹 legend/라벨 단순화:
  - "Center (mm)" → "Receiver Center 좌표 (mm)", 하위 라벨 "Receiver
    center X/Y/Z" → "X/Y/Z".
  - "Offset (mm)" → "Receiver Offset (mm)", 하위 라벨 → "X/Y/Z".
  - "Rotation (deg)" → "Receiver Rotation (deg)", 하위 라벨 → "X/Y/Z".
  - `VectorFields`에 `ariaLabels` prop을 추가해, 화면에 보이는 라벨은
    짧게(X/Y/Z) 줄이면서 접근성 이름은 기존처럼 구분되게
    유지했다(`Receiver center X`/`Receiver offset X`/`Receiver rotation
    X`) - 세 그룹이 전부 "X"라는 짧은 라벨을 쓰게 되어 스크린리더나
    `getByRole(name:)` 조회가 충돌하는 걸 막기 위함.
  - "Receiver width (mm)"/"Receiver height (mm)" → "Receiver Size
    (mm)" fieldset 아래 "Width (mm)"/"Height (mm)"로 통합.
  - "Resolution X"/"Resolution Y"/"Acceptance angle (deg)"는 문구
    그대로 유지하되, 같은 행(3열)에 배치.
- **Tilt pivot 섹션 전체 삭제** (Receiver center/Custom point 토글,
  좌표 입력, "뷰어에서 좌표 선택" 버튼). Rotation은 항상 Center +
  Offset 위치, 즉 receiver 자기 자신을 기준으로 그 자리에서 회전하는
  것으로 고정했다 - 어제 추가했던 custom pivot 기능을 다시 뺀 것이다.
  - `pivotMode`/`pivot` state, 관련 로드/저장 로직, 공용
    `pivotPickArmed`/`pivotPickPoint`/`pivotPreviewPoint` 구독을
    ReceiverDialog에서 제거했다. `createDatumReceiver()`는 pivot
    인자를 생략하면 기존처럼 `null`(제자리 회전)로 동작하므로 별도
    수정 없이 그대로 호출한다.
  - component Transform editor의 Tilt pivot 기능 자체는 그대로
    남아있다 - 이번 삭제는 Receiver 다이얼로그 한정.

## 검증

- `tsc -b` 통과, `vitest run` 18 files / 94 tests 통과(기존 스위트에
  Receiver custom pivot UI를 직접 건드리는 테스트가 없어서 회귀 없음 -
  `createDatumReceiver()` 자체의 pivot 수식 테스트는 함수가 그대로
  남아 있어 계속 통과).
- 실제 브라우저로 Datum plane receiver 다이얼로그를 열어 새 레이아웃을
  스크린샷으로 확인: Receiver Center/Offset/Rotation 모두 X/Y/Z로,
  Tilt pivot 박스 없음, Receiver Size (mm) 아래 Width/Height, Resolution
  X/Y·Acceptance angle이 한 행에 배치됨.
