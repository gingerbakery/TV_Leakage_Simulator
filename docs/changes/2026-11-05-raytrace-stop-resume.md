# Ray Tracing Stop 후 동일 조건 이어 실행

2026-11-05 · Source checkout `5cdf3a1` 기반 미커밋 변경.

## 동작

일반 Ray Tracing을 중단하면 완료된 primary Ray/묶음 경계에서 부분 결과를 반환하고 실제 계산 worker를 대기시킨다. 동일한 요청을 다시 시작하면 새 Job ID를 발급하되 같은 worker의 계산 프레임을 이어간다. 광원 난수/iterator, 현재 광원과 묶음 내 위치, 누적 grid·제곱합·경로, 준비된 mesh/BVH와 실행 컨텍스트를 유지한다. 완료 Ray를 다시 생성하거나 추적하지 않으며 준비 함수를 다시 호출하지 않는다.

Stop 시 현재 묶음을 마칠 수 있으므로 클릭 직후의 화면 수치보다 보관 Ray 수가 조금 증가할 수 있다. 안전한 누적 경계에서 멈추기 위한 동작이다. 중단 부분 결과는 살아 있는 누적 객체와 분리해 계속 계산한 뒤에도 변경되지 않는다. Native retained summary는 보관 전에 host 결과에 반영한다. 연속 실행과 비교할 때 누적 경계의 부동소수점 합산 순서 차이는 가능하므로 물리량 허용 오차와 이산 일치를 구분한다.

UI에는 같은 Case·설정의 중단 작업에 **이어서 실행**과 **처음부터**를 제공한다. 서버는 전체 요청의 정렬된 JSON SHA-256을 다시 검사한다. 광원·Receiver·재질/BSDF·ROI·Block·변환·seed·Ray 수·연산 설정·scene token 등이 바뀌면 새 계산으로 시작한다. 화면의 확인만 믿고 다른 입력의 계산 상태를 재사용하지 않는다. `resume_scope`에 Case ID를 넣어 동일 도면/이름의 서로 다른 Case도 구분한다.

재개 응답은 `resumed_from_job_id`, `resume_count`와 기존 처리 Ray 수를 제공하며 `preparing`으로 돌아가지 않는다. 재개 후 다시 Stop할 수 있다. 중단 결과는 `phase=stopped`, `resume_available=true`로 표시하고 실제 중단 비율을 사용한다. 완료 상태라는 이유로 100%를 표시하던 UI 분기도 수정했다.

자동 수렴은 현재 구간의 배수·seed 인덱스와 그 구간 **이전** 누적/이력을 복원한다. 보관했던 부분 구간을 완성한 뒤 이전 부분 결과를 다시 더하지 않는다. 결과 수신 대기 중 과거 부분 Job을 재처리하지 않도록 중복 처리 방지도 유지한다.

## 수명과 제한

- 업데이트 후 시작한 작업부터 보관된다. 과거 버전에서 이미 종료한 부분 결과를 소급 재개하지 않는다.
- 서버 프로세스 메모리의 단일 보관 상태다. 서버 종료·재시작, 다른 비동기 해석 시작, 변경된 입력으로 실행, 명시적 처음부터, 작업 이력 삭제 시 해제한다. `.bitsam`이나 디스크에 실행 프레임을 저장하지 않는다.
- 보관 중 계산은 진행하지 않지만 계산 메모리는 유지한다. 교체할 때 이전 worker를 깨워 종료하고 자원을 정리한 뒤 새 계산을 준비한다. 연속 start/취소에서도 이전 정리 작업을 기다리도록 선행 작업 관계를 유지한다.
- 준비 단계 중단은 완료 Ray가 없으므로 기존 취소 동작을 유지한다. 요청 Ray가 모두 끝난 뒤에는 불필요한 100% checkpoint를 만들지 않는다.
- 최초 구현 대상은 **일반 Ray Tracing의 현재 실행 구간**이었다. 후속 변경에서 [Preview 관찰 휘도 역추적](2026-11-05-preview-radiance-stop-resume.md)도 지원한다. Preview 경로 탐색 모드와 Batch 전체 스케줄/각도 sweep 전체 재시작 복구는 대상이 아니다. 자동 수렴의 현재 구간 복원은 같은 패널의 실행 상태가 유지될 때 지원한다.
- GPU 실행 컨텍스트도 같은 worker에 남기도록 공통 경로에 연결했으나, 이번 검증은 CPU 기준이다. NVIDIA 실기 재개·성능·CPU/GPU parity를 검증했다고 주장하지 않는다. GPU 준비/실행 상태 판정은 기존 production preflight와 실제 CUDA 성공 수 기준을 그대로 따른다. Driver/Toolkit 설치나 사용자 서버 재시작은 수행하지 않았다.

## 검증

- 새 CPU 재개 검사 7건: scalar/batch 반복 중단, 다중 광원·Face·MIS, 수광 grid/제곱합·hit 수·저장 경로, 부분 결과 분리, 같은 요청의 builder/runner 재진입 방지, 반복 API 재개, 입력·Case scope 변경/처음부터, 보관 이력 삭제 후 worker 해제.
- 이산 수치와 저장 경로는 연속 실행과 같고, 검증 fixture의 grid/제곱합은 절대 오차 `1e-12` 이내다. 이는 합성 CPU fixture의 구현 회귀 기준이며 실물 TV 정확성의 증거가 아니다.
- 관련 backend/API·기존 관찰/Block·RT2A·종료 정책 검사 76건 통과.
- Ray Tracing UI 검사 27건 통과. 이어 실행/처음부터/입력 변경 분기와 자동 수렴 구간의 중복 누적 방지, 중단 비율 표시를 포함한다.
- TypeScript와 production build 통과. 기존 큰 bundle 안내는 남아 있다.

```powershell
$env:PYTHONPATH='src;tests'
.\.venv\Scripts\python.exe -m unittest test_raytrace_resume test_api_fastapi test_observer_full_detail test_observer_radiance test_blocker_transport test_raytracer_rt2a test_termination_policy -q
# frontend 폴더
npm.cmd test -- src/features/raytracing/ray-tracing-auto-convergence.test.tsx
npm.cmd run build
```

최종 메타데이터/도움말 문구 정리 뒤 새 재개 검사 7건과 production build를 재실행해 통과했다. 커밋·푸시는 수행하지 않았다.
