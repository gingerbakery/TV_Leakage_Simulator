# Emitter/Receiver 적용 양식 통일

## 목표

- Emitter와 Receiver의 생성 버튼 및 Properties popup 동작을 동일한 규칙으로 맞춘다.

## 구현 내용

- Web UI 버전을 `v0.9.4`로 갱신했다.
- Receiver 생성 방식의 첫 번째 버튼만 파란색으로 표시하던 전용 CSS override를 제거했다.
- Datum plane, Reference geometry, Current view 버튼은 Emitter와 동일하게 모두 흰색으로 시작한다.
- Receiver Properties에서 저장 전에는 `Discard draft` 버튼을 표시한다.
- 저장된 Receiver를 편집할 때는 동일한 위치에 `Delete receiver` 버튼을 표시한다.
- Draft 폐기와 저장 Receiver 삭제 후 안내 문구를 Emitter와 동일한 방식으로 표시한다.

## 검증

- Receiver 전용 primary/secondary 색상 override가 제거되었는지 확인했다.
- Python 문법 검사와 전체 단위 테스트 10개가 통과했다.
