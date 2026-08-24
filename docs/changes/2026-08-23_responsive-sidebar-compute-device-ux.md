# 반응형 Workflow 사이드바와 연산 장치 선택 UX

## 사용자 문제

- 고정된 Workflow 폭과 내부 컨트롤의 최소 폭이 충돌해 오른쪽 내용이 잘렸다.
- 사이드바 폭을 넓히거나 줄일 방법이 없었다.
- CPU/GPU 선택, 준비 상태, BVH 설정이 `Run Options` 안팎에 중복되어 전환할
  때 레이아웃이 크게 움직이고 전문 용어가 기본 화면을 차지했다.

## 변경

- Workflow 기본 폭을 384px로 조정하고 320~560px 범위의 마우스 드래그,
  키보드 방향키/Home/End, 기본/넓게 보기 토글을 추가했다.
- 선택한 폭을 브라우저에 저장하고 화면 폭이 줄면 안전 범위로 자동 보정한다.
- Sidebar, Accordion과 중첩 폼에 `min-width: 0`/`max-width: 100%` 계약을
  적용하고 음수 여백과 intrinsic-width 기반 ScrollArea를 제거했다.
- 좁은 화면에서는 Workflow가 전체 폭으로 쌓이고 다열 입력과 Viewer toolbar가
  가로 스크롤 없이 재배치된다.
- Ray Tracing 최상단에 독립 `연산 장치` 영역을 만들고 동일 크기 CPU/NVIDIA
  GPU 버튼과 고정 높이 상태 행만 노출한다.
- GPU 준비 완료 상태는 `준비 완료 · <GPU 이름>`만 표시한다. 좁은 폭에서는
  고정된 두 행에 나눠 전체 모델명을 보존한다. Compute capability, FP64,
  production kernel, provider, Numba와 Toolkit은 정보 tooltip에서만 확인한다.
- `Acceleration structure`는 `Run Options > 고급 옵션`으로 이동했다. 일반
  사용자는 자동 최적화를 유지하고 GPU 선택 시 호환 BVH 설정이 자동 적용된다.
- GPU readiness는 production Ray/BVH scope, strict FP64, kernel 실행·검증과
  provider contract를 모두 만족할 때만 성공으로 판정한다.

## 회귀 방지

- Sidebar 폭 토글·키보드 조절·저장 계약 테스트
- CPU/GPU 버튼 접근성·고정 높이 상태·기술 정보 숨김 테스트
- GPU 선택 시 `compute_backend=gpu_cuda`와 BVH 정상화 테스트
- 미검증 GPU 실행 차단과 CPU 복귀 테스트
- 여러 browser viewport의 document/sidebar horizontal overflow 검사
