# Frontend Viewer XYZ orientation gizmo

## 문제

- 기존 `AxesHelper`가 모델 중심에 있어 import한 CAD surface에 가려졌다.
- 모델 크기와 카메라 확대율에 따라 축이 너무 작거나 내부에 묻혔다.
- React Viewer에는 기존 UI의 `Axis size` 조절 기능이 이식되지 않았다.

## 수정

- 모델 scene과 분리된 orientation scene·orthographic camera를 추가했다.
- main scene을 렌더링한 뒤 depth를 비우고 Viewer 좌하단에 XYZ gizmo를
  별도 viewport로 렌더링한다.
- X red, Y green, Z blue shaft·arrow head·문자 label을 표시한다.
- main camera의 position·up을 따라 gizmo 방향을 동기화한다.
- 상단 도구막대에 `Axis size` 슬라이더를 복원했다.
  - 범위: 50%~100%
  - 기본값: 50%
  - 새 50%는 기존 150%와 같은 168px 기준 크기이며, 새 100%는
    그 두 배인 336px 기준 크기다.
  - 화면 픽셀 기준 크기만 변경하며 모델 카메라는 변경하지 않는다.
- gizmo geometry·label texture를 Viewer cleanup 때 함께 해제한다.

## 검증

- `npm run typecheck`
- `npm run lint`
- `npm test` — 8 files, 28 tests, Axis size 50%~100% 범위 확인
- `npm run build`
- `npm audit --audit-level=high` — 취약점 0건
- 우측 하단 STEP(50,944 faces, 4 components) Chrome 검증
  - 모델 앞·뒤·측면 회전 중 XYZ 방향 동기화
  - CAD에 의한 축 가림 없음
  - 50%·100%·150% 크기 변경
  - camera preset·component 상태 유지
  - console error·warning 없음
