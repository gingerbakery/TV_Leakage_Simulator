# 숫자 입력 공란·음수·소수 편집 개선

## 목적

- 좌표와 작은 gap 값을 입력할 때 기존 `0`을 먼저 지워야 하는 불편을 줄인다.
- controlled number input이 공란을 즉시 `0`으로 바꾸면서 음수와 소수점 입력을 방해하는 문제를 해결한다.
- 좌표, 이동·틸트, Emitter·Receiver, Ray 설정의 숫자 입력 동작을 통일한다.

## 공통 동작

- 값이 `0`인 입력창을 클릭하면 편집 중에는 공란으로 전환한다.
- 입력 중 `공란`, `-`, `.`, `-.` 같은 미완성 상태를 허용한다.
- 유효한 음수·소수·지수 표기 값을 입력하면 즉시 preview와 상태에 반영한다.
- 공란 또는 미완성 값에서 포커스를 벗어나거나 Enter를 누르면 `0`으로 확정한다.
- 값이 `0`이 아니면 포커스 시 전체 선택하여 바로 덮어쓸 수 있다.
- 위·아래 방향키는 설정된 step을 기준으로 값을 증감한다.

## 적용 범위

- ROI X/Y/Z 좌표
- Component 및 local face의 Move X/Y/Z
- Tilt Rx/Ry/Rz
- Emitter center, rotation, width, height, power, ray count, Gaussian sigma
- Receiver center, rotation, offset, tilt, size, resolution, acceptance angle, view distance
- Ray tracing의 ray 수, 최대 반사 횟수, random seed, minimum energy, 저장 path 수

## 검증

- 공통 숫자 입력 단위 테스트 3개 추가
- frontend 전체 13개 test file, 64개 테스트 통과
- TypeScript 및 Vite production build 통과
- 실제 브라우저에서 ROI `0 → 공란 → -0.25` 입력 확인
- 실제 브라우저에서 최대 반사 입력 `공란 → 포커스 이탈 → 0` 복귀 확인
