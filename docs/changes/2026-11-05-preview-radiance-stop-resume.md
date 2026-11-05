# Preview 관찰 휘도 역추적 Stop / Resume

2026-11-05 · Source checkout `b2a14d5` 기반 후속 변경. 커밋·푸시 전.

## 수정 내용

기존 이어 실행은 일반 Ray Tracing만 지원했다. Preview의 `measurement=radiance` 요청도 같은 worker를 보관하도록 확장했다. 빠른 격자 검색과 고정 간격 전체 정밀 검색, 국소 각도 및 180° 영역이 같은 역추적 경로를 사용한다.

- 광원 추적 도중 Stop하면 완료한 primary Ray와 난수/누적 grid를 보관한다. 역추적 시작 전에도 유효한 빈 휘도 부분 결과를 반환한다.
- 관찰 역추적 도중 Stop하면 완료 각도·셀의 누적값뿐 아니라 진행 중인 반사/광원 가시성 Ray generator, Ray별 난수, 대기 교차 쿼리와 아직 합산하지 않은 완료 슬롯을 보관한다. 이미 끝난 광원 추적이나 BVH 준비로 돌아가지 않는다.
- 교차 wave 경계에서 멈춘다. 진행 중인 동기 교차 호출 자체를 중간 취소하지 않으므로 Stop 직후 대기할 수 있다.
- 부분 결과는 분리된 복사본이다. 완료 묶음만 집계한 `attempted_count`를 진행률에 사용하고 `in_flight_sample_count`에는 생성했지만 아직 집계하지 않은 표본을 기록한다. 여기에는 이미 교차 계산을 끝내고 묶음 합산을 기다리는 슬롯도 포함한다.
- 총 진행량은 광원 Ray 수 + 관찰 표본 수다. 재개 시 기존 수치부터 이어간다. 광원 기여도 요약을 이미 정리한 뒤 역추적에서 멈춰도 기여도를 중복 정리하지 않는다.
- Preview에 **휘도 역추적 이어 실행**과 **Preview 처음부터**를 제공한다. 보관 상태는 짧게 표시하고 수명/제한 설명은 닫힌 `(?)` 도움말에 넣었다.

## 수명과 제한

서버는 전체 요청 JSON 해시와 Preview Case scope를 검사한다. 광원·재질·형상·Block·각도·검사 간격·반복 수·seed 등 요청 입력이 바뀌면 새 계산이다. 같은 서버 메모리에서만 보관하며 서버 종료/재시작, 다른 해석 실행, 이력 정리, 명시적 처음부터로 해제된다. 보관 중 메모리는 유지하지만 계산은 진행하지 않는다. 업데이트 이전에 이미 끝난 작업이나 저장된 `.bitsam` 부분 결과를 소급 재개하지 않는다. 경로 탐색(`path_support_counts`) 모드는 이번 범위에 포함하지 않는다.

## 검증 범위

새 CPU 검사는 scalar/batch 역추적의 반복 중단, 집계 전 Ray와 부분 셀, 분리된 부분 결과, 연속 실행 대비 최종 셀/각도/휘도/경로 일치, 교차 호출 수 동일, 광원 grid/상세 기여도 보존을 확인한다. API 검사는 같은 요청에서 builder와 runner가 각각 한 번만 호출되고 합산 진행량이 이어지는지 확인한다. UI 검사는 관찰 시작 전 부분 결과 수용 및 이어 실행/처음부터/입력 변경 요청을 구분한다.

이는 합성 fixture의 구현 회귀 검증이며 실물 TV 광학 정확성이나 GPU 재개 성공의 증거가 아니다. NVIDIA 실기 검증, 설치, 사용자 서버 재시작은 수행하지 않았다.

검증 결과: 관련 backend 회귀 79건 통과 후, 180° 격자 검색의 각도 경계 재개 검사를 추가해 신규 재개 검사 4건을 다시 실행하고 모두 통과했다(서로 다른 backend 검사 총 80건). Preview/일반 Ray Tracing UI 51건과 TypeScript 포함 production build도 통과했다. 기존 bundle 크기 안내는 남아 있다. `git diff --check` 통과. 커밋·푸시는 수행하지 않았다.

```powershell
$env:PYTHONPATH='src;tests'
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m unittest test_preview_radiance_resume test_raytrace_resume test_api_fastapi test_observer_full_detail test_observer_radiance test_blocker_transport test_raytracer_rt2a test_termination_policy -q
# frontend 폴더
npm.cmd test -- src/features/leak-preview/leak-preview-panel.test.tsx src/features/raytracing/ray-tracing-auto-convergence.test.tsx
npm.cmd run build
```
