# Emitter Datum Plane에 CAD Face pick 추가 (Receiver와 동일 패턴)

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 배경

Receiver Datum Plane에만 있던 "뷰어에서 CAD Face 선택" 기능과 라벨
네이밍 패턴을 Emitter Datum Plane에도 동일하게 반영해 달라는 요청.

## 변경 사항

### Store 공용화

Receiver 전용이었던 `receiverFacePickArmed`/`receiverFacePickResult`
(+ `ReceiverFacePickResult` 타입)를 `datumFacePickArmed`/
`datumFacePickResult`(+ `DatumFacePickResult`)로 이름을 바꿔 범용화했다.
Emitter Datum Plane과 Receiver Datum Plane이 이제 완전히 같은 채널을
공유한다 - 둘 다 "얼굴 하나 클릭해서 중심점 + 법선을 얻는다"는 같은
요구라서 굳이 나눌 이유가 없었다.

- `workspace-store.ts`: 필드/액션/selector 이름 변경.
- `three-viewer-canvas.tsx`: 클릭 처리 로직과 상태 메시지("Receiver
  face picking" → "Datum face picking")를 Receiver 전용 표현에서
  중립적인 표현으로 변경. 로직 자체(클릭한 삼각형의 정확한 표면
  교차점을 center로, 그 삼각형의 normal을 반환)는 그대로.

### `EmitterDialog` (`datum_plane` 모드)

- "뷰어에서 CAD Face 선택" 버튼 추가 - Receiver와 동일하게 armed
  상태에서 클릭한 CAD 표면 교차점 + normal을 Center/Rotation에 채운다.
- 라벨 패턴을 Receiver와 통일:
  - "Center (mm)" (라벨 자체가 `Center X/Y/Z`였음) → "Emitter Center
    좌표 (mm)", 하위 라벨 X/Y/Z (접근성 이름은 `Emitter center X/Y/Z`로
    유지).
  - "Rotation (deg)" → "Emitter Rotation (deg)", 하위 라벨 X/Y/Z.
  - "Emitter width (mm)"/"Emitter height (mm)" → "Emitter Size (mm)"
    fieldset 아래 "Width (mm)"/"Height (mm)".
- Emitter에는 Receiver의 Offset/Tilt pivot 같은 추가 기능은 넣지
  않았다 - 이번 요청 범위는 face pick 메커니즘과 네이밍 패턴 통일까지.

## 검증

- 백엔드 93개 테스트 통과(영향 없음, 프런트 전용 변경).
- 프런트 `tsc -b` 통과. `vitest run` 18 files / 94 tests 통과 - 기존
  Emitter Datum Plane 테스트가 예전 접근성 이름(`Center X`)을 참조하고
  있어서 새 이름(`Emitter center X`)으로 갱신했다.
- 실제 브라우저로 Emitter Datum Plane 다이얼로그를 열어 CAD Face 클릭
  → Center 좌표가 정확히 채워지는 것, 레이아웃이 Receiver Datum
  Plane과 동일한 패턴인 것을 스크린샷으로 확인했다.
