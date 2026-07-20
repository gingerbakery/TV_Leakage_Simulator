# Receiver Position / Tilt Adjustment

## 목표

- Reference Geometry와 Current View Receiver를 1차 생성한 뒤 위치와 방향을 수치로 미세 조정한다.

## 구현 내용

- Web UI 버전을 `v0.9.5`로 갱신했다.
- Receiver Properties에 기본 닫힘 상태의 `Position / Tilt adjustment` 탭을 추가했다.
- Reference Geometry와 Current View 방식에서만 adjustment 탭을 표시한다.
- `Offset X/Y/Z (mm)`로 월드 좌표계 이동량을 입력한다.
- `Tilt X/Y/Z (deg)`로 월드 X→Y→Z 순서의 회전을 입력한다.
- 입력값은 즉시 3D preview의 Receiver 위치와 수광 방향에 반영된다.
- `Apply` 시 기준면, offset/tilt, 최종 center/U/V/normal을 함께 저장한다.
- 저장된 Receiver를 다시 편집해도 변환이 중복 누적되지 않도록 기준면과 추가 변환값을 분리했다.
- Direct ray tracing에는 adjustment가 반영된 최종 Receiver plane을 전달한다.

## 데이터 계약

- 기준면: `base_center`, `base_u_axis`, `base_v_axis`, `base_normal`
- 이동량: `position_offset_mm`
- 회전량: `tilt_xyz_deg`
- 계산용 최종값: `center`, `u_axis`, `v_axis`, `normal`

## 검증

- Receiver 데이터 계약 round-trip 테스트에 기준면과 offset/tilt 필드를 추가했다.
- Current View Receiver에 Offset Z `5 mm`, Tilt Y `10°`를 입력해 center와 normal이 즉시 변경되는 것을 확인했다.
- 저장 payload에 기준면, 최종면, `position_offset_mm`, `tilt_xyz_deg`가 함께 기록되는 것을 확인했다.
- Reference Geometry Receiver에서도 adjustment 탭이 표시되는 것을 확인했다.
- 브라우저 warning/error 로그가 없는 것을 확인했다.
- Python 문법 검사와 전체 단위 테스트 10개가 통과했다.
