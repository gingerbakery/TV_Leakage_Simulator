# 좌표 입력 필드를 소수 첫째자리까지만 표시

- 날짜: 2026-08-05
- 대상 브랜치: `main`

## 배경

CAD Face 선택, pivot 클릭 등으로 좌표가 자동 채워질 때
`231.99999999998317`, `44.99999999999825`처럼 부동소수점 연산
잔여 오차가 그대로 입력창에 노출되는 경우가 있었다. 이를 포함해
툴 전체의 좌표 입력값을 소수 첫째자리까지만 보이도록 통일해 달라는
요청.

## 변경 사항

`frontend/src/components/ui/number-input.tsx`(공용 `NumberInput`)에
`decimals?: number` prop을 추가했다.

- 포커스가 없을 때 보여주는 값과, blur·Enter·화살표 키로 커밋되는
  값 모두 `decimals`가 주어지면 그 자리수로 반올림한다.
- 타이핑 중(포커스 상태)에는 draft 텍스트를 그대로 두어, 사용자가
  입력 중인 글자 수를 강제로 자르지 않는다 - 포커스를 벗어나는
  순간에만 반올림된 값으로 스냅된다.
- `decimals`를 안 넘기면 기존 동작(반올림 없음) 그대로라, Width/
  Height/Resolution/Acceptance/Material 수치 등 "좌표"가 아닌 다른
  숫자 입력에는 영향이 없다.

`decimals={1}`을 적용한 곳(전부 X/Y/Z 좌표·회전 그룹):

- `ray-tracing-panel.tsx`의 `VectorFields` (Emitter/Receiver의
  Center, Offset, Rotation/Tilt 전부 여기로 통일되어 있어 한 곳만
  고치면 됨).
- `transform-editor-dialog.tsx`의 `VectorEditor`(Move, Tilt)와
  Pivot 커스텀 포인트 X/Y/Z.
- `roi-selection-panel.tsx`의 "좌표로 Face 찾기" X/Y/Z.

## 검증

- 프런트 `tsc -b` 통과. `vitest run` 18 files / 96 tests 통과
  (2개 추가: `NumberInput`의 반올림 표시·타이핑 중 정밀도 유지
  테스트; 기존 pivot/receiver 좌표 어서션 2건을 `12.0`/`10.0`/
  `0.0` 형태로 갱신).
- 실제 브라우저(Playwright + 로컬 Chrome)로 Receiver Datum Plane의
  "뷰어에서 CAD Face 선택"으로 클릭 → Center/Offset/Rotation 전
  필드가 `400.0`/`0.0`/`39.0`/`90.0`/`180.0`처럼 항상 소수 첫째
  자리까지만 표시되는 것을 확인. Width/Height/Resolution/
  Acceptance는 기존처럼 반올림 없이 그대로 표시됨도 함께 확인.
