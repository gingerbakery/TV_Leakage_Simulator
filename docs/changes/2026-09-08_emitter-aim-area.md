# 2026-09-08 — 목표 영역 한정 발광 v1 구현·검증

## 범위와 작업 경계

- 기능 버전: `aim-area.v1`, 계산 계약 `target_only_uniform_area_v1`.
- 소스 전달 경로: Git source checkout. `TV leakage simulator main` 작업트리, 기준 커밋 `d991858b0626c8bd2f2b713b14d385d82ffab5c1`.
- 기존 최대 반사 1,000회 변경을 보존했다. 별도 렌더링 작업트리에는 수정하지 않았다.
- 이번 작업에서 Commit/Push/Release/EXE 또는 ZIP 배포를 수행하지 않았다.
- 최신 사용·물리 계약: `docs/emitter-aim-area.md`. 이전 검토안은 `docs/emitter-aim-area-feasibility.md`에 이력으로 보존한다.

## 구현 내역과 코드 추적

| 변경 | 관련 파일 |
| --- | --- |
| optional Aim 계약, 유효성 검사, .bitsam 호환 | `src/leakage_simulator/types.py`, `frontend/src/api/types/raytrace.ts`, `frontend/src/features/projects/bitsam-project.ts` |
| 면적 균일 Target 샘플링, 중첩 방지 | `src/leakage_simulator/aim_sampling.py` |
| Face/Datum/Reference polygon CPU 배치·scalar 및 CUDA 초기 ray 연결 | `src/leakage_simulator/fast_sampling.py`, `src/leakage_simulator/raytracer.py` |
| 기존 Emitter 팝업에 닫힌 Aim 설정, 기존 Apply/Enter/Cancel 유지 | `frontend/src/features/raytracing/emitter-aim-editor.tsx`, `frontend/src/features/raytracing/ray-tracing-panel.tsx` |
| 사각형/원형 World Target, 외곽선/안내선, CAD picking 방해 방지 | `frontend/src/features/raytracing/emitter-aim.ts`, `frontend/src/features/viewer/emitter-aim-overlay.ts`, `frontend/src/features/viewer/three-viewer-canvas.tsx` |
| Aim 차이를 결과 비교의 조건 불일치로 표시 | `frontend/src/features/results/result-window.tsx` |
| 물리·계약·ROI/Transform 회귀 | `tests/test_emitter_aim.py`, `tests/test_raytrace_bridge.py` |
| UI·저장·결과 비교 회귀 | `frontend/src/features/raytracing/emitter-aim.test.ts`, `frontend/src/features/raytracing/ray-tracing-auto-convergence.test.tsx`, `frontend/src/features/results/result-ui.test.tsx` |
| 실제 GPU/CPU 반복 검증 도구 | `scripts/verify_emitter_aim.py` |

원래 광원의 기대값을 보존하는 MIS가 아니라 목표 방향으로 전체 입력 광량을 배분하는 광원 모델이다. `SET luminance = 500`은 그대로 500을 보관하며, `π L A_source` 총광속 식을 바꾸지 않았다. 임의 nit 보정값을 추가하지 않았다.

## 단계별 검증 결과

| 단계 | 시험 | 결과 |
| --- | --- | --- |
| AIM-0 | .bitsam 왕복, 구버전 Aim 누락=Off, 잘못된 크기/축 거절 | 통과 |
| AIM-1 | 접힌 기본 메뉴, 프리뷰/확정/취소, Target 표시, 좌표·tilt·Reset, 방향 설정 복원 | 통과 |
| AIM-2 | 면적 분포·단위벡터·광량 보존, 중간 차폐, 경면 반사, primary MIS 중복 방지 | 전용 단위 테스트 12개 통과 |
| AIM-3 | 3종 광원 × 2종 Target × 직접/경면/차폐 × CPU/GPU × 첫 실행+warm 2회 | 18개 장면, 108회 실행 전부 통과 |
| 연동 회귀 | 광원·ROI·Transform·MIS·수광 광속·다회반사 관련 Python 시험 | 55개 통과, 15.384초 |
| API 회귀 | FastAPI scene·job·입출력 계약 | 18개 통과, 0.526초 |
| UI 회귀 | 프런트엔드 전체 Vitest | 33파일 / 209개 통과, 최종 9.60초 |
| 정적 검증 | TypeScript 및 production build | 통과 |
| UI 실제 화면 | 합성 TV 코너 STEP import, Datum Aim 원형·좌표·tilt·프리뷰·확정 | 브라우저 확인, console error/warn 없음 |

브라우저 검증은 별도 테스트 탭에서 수행했다. 본래 사용자 탭은 빈 작업공간임을 확인한 뒤 업데이트했으며 사용자 CAD/설정은 덮어쓰지 않았다.

## CUDA 환경 및 실행 증거

- Source launcher: `run_web_gpu.ps1 -PreflightOnly`, 서버는 `run_web_gpu.ps1 -Port 8788`.
- 읽기 전용 inventory: `setup_windows_gpu.bat` 래퍼가 도구 환경에서 구문 오류를 내어 같은 공식 `setup_windows_gpu.ps1`의 기본 CHECK ONLY 모드 사용.
- NVIDIA GeForce RTX 3070, compute capability 8.6.
- NVIDIA driver 591.59, CUDA Toolkit 13.1.1, Python 3.13.3 x64.
- Node.js 24.14.0 / npm 11.9.0, numba 0.66.0.
- 드라이버/Toolkit 신규 설치, 시스템 설정 변경, 재부팅 없음. 공식 런처로 기존 프로젝트 의존성 동기화 및 프런트엔드 재빌드.

| Production preflight 필드 | 값 |
| --- | --- |
| available | true |
| strict_float64 | true |
| kernel_executed | true |
| kernel_verified | true |
| preflight_scope | production_ray_bvh |
| provider_contract | strict_float64_bvh_v1 |

검증 광원: `face`, `datum_plane`, `reference_plane + polygon_auto`. Target: rectangle/circle. 각 실행 8,192 rays, 최대 반사 2, specular ρ=0.8, 각도별 반사율 Off, 저장 경로 최대 20개.

| GPU 요청 54회 집계 | 값 |
| --- | --- |
| compute_execution_state | 모두 gpu_active |
| compute_execution_reason | 모두 null |
| CUDA batch attempt / success | 54 / 54 |
| CPU hybrid success | 0 |
| Intersection fallback | 0 |
| Resident wavefront fallback | 0 |

**이 시험에서 실제 CUDA 실행을 확인했다.** 초기 발광 샘플 배열 준비, CAD import, UI 전체가 GPU화되었다는 의미는 아니다. 일반 장면에서 작은 wave가 CPU hybrid로 실행되는 기존 정책은 유지한다.

## 수치 검증 및 시간

입력 광속 1 lm, 직접 수광면은 모든 진행광을 받도록 배치했다. 경면 사례는 한 번 반사한 뒤 모든 반사광을 받으며, 차폐 사례는 반사율 0의 불투명 차폐면을 앞에 놓았다.

| 기준 장면 | 이론 Receiver flux | CPU/GPU 결과 |
| --- | --- | --- |
| 차폐 없는 Target 방향 발광 | 1 lm | 일치 |
| ρ=0.8 경면 1회 반사 | 0.8 lm | 일치 |
| 중간 불투명 차폐 | 0 lm | 일치 |

- 108회 전체에서 이론 총광속 대비 최대 절대 오차: `1.43 × 10⁻¹⁴ lm` 미만.
- 같은 seed·장면의 CPU/GPU Receiver cell 광속 차이: 이 시험에서 모두 0.
- Face/Rectangle 직접광 CPU: 첫 3.626초 → warm 0.044 / 0.044초.
- 같은 장면 GPU: 첫 3.468초 → warm 0.042 / 0.047초. 첫 GPU 실행의 resident JIT 약 3.368초.
- 모든 GPU warm 실행 범위: 약 0.033–0.176초.

이는 작은 합성 모델의 정확도/경로 검증이며, 대형 TV 모델 성능이나 LT/실측 정합성을 보증하는 수치가 아니다. 같은 프로세스·같은 장면별 첫 실행과 두 번의 warm 실행을 분리해 기록했다.

원시 실행 기록: `outputs/emitter-aim-verification.json`. 시험 실행 조건·전체 성능 필드를 포함하며, outputs 산출물은 기본적으로 Git 추적 대상이 아니다.

## 재현 명령

프로젝트 작업트리 PowerShell에서 공식 source GPU 준비 후 실행한다.

```powershell
.\run_web_gpu.bat -PreflightOnly
.\.venv-gpu\Scripts\python.exe -m unittest discover -s tests -p test_emitter_aim.py -v
.\.venv-gpu\Scripts\python.exe scripts\verify_emitter_aim.py
```

## 남은 범위 / 알려진 사항

- CAD surface를 직접 클릭하는 Target, 부품 Transform 추종, Polygon Target은 다음 단계다. 현재 Target은 World 좌표의 사각형/원형이다.
- 원래 Lambertian/Gaussian을 보존하는 조건부 Aim과 중요도 샘플링 모드는 이번 기능과 구분해서 후속 설계한다.
- LT 동일 설정 비교·실측 nit 보정은 아직 수행하지 않았다.
- 기존 결과창의 `liveResult` Hook dependency lint 경고 1개는 변경 범위 밖이라 유지했다. lint error는 0개.
- Vitest의 jsdom Canvas/WebGL 미지원 경고 및 기존 production chunk 500 kB 경고는 남아 있다. 실제 브라우저 Three.js 프리뷰는 별도로 확인했다.
