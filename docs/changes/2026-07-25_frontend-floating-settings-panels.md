# Frontend 설정 패널·배치 미리보기 개선

## 목적

Transform, Material, Emitter, Receiver를 설정하는 동안 대상 CAD와 배치
위치를 계속 확인할 수 있게 한다.

## 변경 내용

- 네 설정창을 화면 중앙 모달 대신 메인 메뉴 다음의 3D Viewer 왼쪽에
  기본 배치되는 공통 플로팅 패널로 통일했다.
- 패널 시작점은 고정 좌표가 아니라 실제 Viewer 경계를 읽어 12px 간격을
  두므로 메뉴 폭이나 화면 크기가 달라져도 메뉴를 덮지 않는다.
- 패널 헤더를 드래그하면 화면 안에서 자유롭게 이동할 수 있다.
- 패널을 연 상태에서도 Viewer 회전·확대/축소·카메라 프리셋과 CAD surface
  선택을 계속 사용할 수 있다.
- Material·Transform 편집 중에는 대상 component를 노란색 면과 edge로
  강조한다.
- Datum Emitter와 Datum·Current View Receiver는 저장 전에도 현재
  좌표·회전·크기의 반투명 기준면과 방향 화살표를 Viewer에 표시한다.
- 동일한 배치 미리보기 상태는 다시 발행하지 않아 카메라 상태와 Receiver
  미리보기 사이의 반복 업데이트를 방지한다.

## 검증

- 실제 `tv_leakage_roi_right_bottom_no_gap.stp`(50,944 faces, 4 components)를
  Chrome에서 Import했다.
- Datum Receiver와 Current View Receiver 패널, 실시간 기준면·방향 표시,
  패널을 연 상태의 Viewer `Fit` 조작을 확인했다.
- Material·Transform 대상 component 강조와 패널 드래그를 확인했다.
- TypeScript typecheck, Oxlint, Vitest 51개를 통과했다.
