# 2026-09-10 — 기존 면광원에 Aim Sphere 추가

## 범위

- 사용자 요청에 따라 점광원·체적 광원 및 새 Emitter 생성 버튼은 추가하지 않았다.
- 기존 CAD Surface/Datum Plane 및 파일 계약에서 지원하는 Reference Plane/Polygon의 초기 각도 설정만 확장했다.
- 작업 위치: `TV leakage simulator main`, `main`, 기준 커밋 `c72a0a9e96026f2b87ffe78d030a0540a4cac0df` 이후 로컬 변경.
- 별도 렌더링 작업트리는 수정하지 않았다. 2026-09-10 구현·검증 시점에는 Commit/Push/Release/ZIP 배포를 하지 않았다.
- 상세 사용·광학 계약: `docs/emitter-aim-sphere.md`.

## 구현 추적

| 변경 | 주요 파일 |
| --- | --- |
| Area/Sphere 선택, 각도 검증, 구버전 Area 기본값 | `src/leakage_simulator/types.py`, `frontend/src/api/types/raytrace.ts` |
| 입체각 균일 scalar/batch 방향, World Alpha/Beta 회전 | `src/leakage_simulator/aim_sampling.py` |
| 기존 CUDA BVH 연결 유지, primary MIS 중복 방지, 실행 계약 기록 | `src/leakage_simulator/raytracer.py` |
| 접힌 Aim 메뉴, 모드별 입력, 4개 프리셋, 간단한 (?) 도움말 | `frontend/src/features/raytracing/emitter-aim-editor.tsx`, `emitter-aim.ts`, `ray-tracing-panel.tsx` |
| 비선택형 구면·원뿔·평행광 가이드, 기존 한쪽 방향 화살표 숨김 | `frontend/src/features/viewer/emitter-aim-overlay.ts`, `three-viewer-canvas.tsx` |
| .bitsam 검증·복원, 결과의 Aim 조건 비교 | `frontend/src/features/projects/bitsam-project.ts`, `frontend/src/features/results/result-window.tsx` |
| 광학·저장 회귀 및 GPU 재현 도구 | `tests/test_emitter_aim_sphere.py`, `tests/test_bitsam_package.py`, `scripts/verify_emitter_aim_sphere.py` |

발광점·원본 CAD·ROI·반사율·Receiver 집계 알고리즘은 변경하지 않았다. 발광 방향을 생성한 뒤 기존 경로를 사용한다. 전방위는 입력 광속을 앞뒤 합계로 배분하며 총량을 두 배로 만들지 않는다.

## 최종 자동 검증

| 검증 | 결과 |
| --- | --- |
| Sphere + 기존 Area/근접 Target + portable .bitsam + FastAPI | Python 57개 통과 |
| scene binary 메타데이터·정렬·JSON fallback | 함수형 테스트 2개 직접 실행, 통과 |
| 프런트엔드 전체 | 41파일 / 287개 통과 |
| TypeScript 및 production build | 통과 |
| CPU/GPU 동등성·광속 보존 | 24장면 × CPU/GPU × 첫 실행+warm 2회 = 144회 통과 |
| 실제 브라우저 UI | 독립 테스트 페이지에서 Light/Dark 940×680, 좁은 화면 360×680 확인 |

Sphere 단위 테스트에는 전방위 100,000방향의 분포·단위벡터, 극각 제한·방향 회전, scalar/batch 고정 난수 비교, 기존 면의 발광점 보존, 기본 분포 난수열 불변, 총광속, 차폐·경면 반사·MIS·SET 등가식 검사를 포함한다.

portable 테스트는 Sphere 설정과 실제 해석 결과를 저장한 뒤 원본 CAD를 제거하고 별도 runtime에서 패키지를 복원한다. 재메시 없이 Receiver grid·저장 Ray 경로가 동일하게 재해석되는지 확인하고, 각도를 바꾼 후 결과가 실제로 달라지는 것도 확인했다.

UI 검증은 실제 EmitterAimEditor와 Three.js overlay를 독립 페이지에서 사용했다. 모드 전환·각도 보존·평행광·기본 접힘·가로 넘침 없음·브라우저 오류 없음을 확인했다. 사용자 작업 탭을 새로고침하거나 덮어쓰지 않았다. 현재 작업 중인 8788 백엔드 재시작은 저장 확인 응답 후 별도로 수행한다.

기존 jsdom Canvas/WebGL 미지원 출력과 production chunk 500 kB 경고는 남아 있다. 기존 결과창 `liveResult` Hook dependency lint 경고 1개는 이번 범위 밖이다.

## CUDA 실행 증거

- 전달 경로: **source checkout**, 공식 `run_web_gpu.ps1 -PreflightOnly` 사용.
- NVIDIA GeForce RTX 3070, compute capability 8.6, numba 0.66.0.
- Toolkit layout: `windows_cuda13_x64_compat`.
- 프로젝트 의존성 동기화 및 production build 수행. OS 드라이버/Toolkit 설치, 시스템 설정 변경, 재부팅은 하지 않았다.
- 테스트 시점 tracked production diff SHA-256: `19eae3e8c14fc128ea882ca9a4015b991bcaae14bea9a3250d195ad37ce91ce5`.

| Production preflight | 값 |
| --- | --- |
| available | true |
| strict_float64 | true |
| kernel_executed | true |
| kernel_verified | true |
| preflight_scope | production_ray_bvh |
| provider_contract | strict_float64_bvh_v1 |

`face`, `datum_plane`, `reference_plane + polygon_auto` 각각 전방위·반구·뒤쪽·고리·평행광·좁은 빔·차폐·경면 반사 장면을 실행했다. 각 실행은 8,192 rays, 동일 장면·동일 seed이며, CPU와 GPU 각각 첫 실행과 두 번의 warm 실행을 기록했다.

| GPU 요청 72회 집계 | 값 |
| --- | --- |
| compute_execution_state | 모두 `gpu_active` |
| compute_execution_reason | 모두 null |
| CUDA batch attempt / success | 72 / 72 |
| CPU hybrid success | 0 |
| Intersection fallback / Resident fallback | 0 / 0 |
| Receiver cell 광속 CPU/GPU 최대 차이 | 이 시험에서 0 lm |
| Ray 수·Receiver hit·surface hit CPU/GPU 비교 | 전부 정확히 일치 |

검증의 무차폐 장면은 전체 진행광을 받는 수광 배치로 1 lm, 불투명 차폐는 0 lm, 반사율 0.8 경면 1회 반사는 0.8 lm을 확인했다. 허용 절대 오차는 1e-10 lm이다.

### 대표 시간 — 작은 합성 모델

| Face 장면 / 장치 | 첫 실행 | warm 1 | warm 2 |
| --- | --- | --- | --- |
| 전방위 / CPU | 3.862초 | 0.051초 | 0.051초 |
| 전방위 / GPU | 3.655초 | 0.077초 | 0.059초 |
| 경면 1회 / CPU | 0.063초 | 0.066초 | 0.059초 |
| 경면 1회 / GPU | 0.052초 | 0.036초 | 0.041초 |

첫 실행에는 초기화/JIT 비용이 포함될 수 있다. 이 결과는 GPU가 모든 작은 장면에서 빠르다는 뜻이 아니며, 대형 TV·1억 rays 처리 속도나 LT/실측 정합성을 보증하지 않는다. 초기 Ray 배열 준비는 CPU/NumPy이고, 기존 CUDA BVH/resident wavefront를 사용한다. 일반 장면의 작은 wave CPU hybrid 정책도 유지한다.

## 재현 및 산출물

프로젝트 PowerShell에서 GPU runbook에 따라 공식 사전 검증 후 실행한다.

```powershell
.\run_web_gpu.bat -PreflightOnly
$env:PYTHONPATH = 'src;tests'
.\.venv-gpu\Scripts\python.exe -m unittest test_emitter_aim_sphere test_emitter_aim test_emitter_aim_proximity test_bitsam_package test_api_fastapi
.\.venv-gpu\Scripts\python.exe scripts\verify_emitter_aim_sphere.py
Set-Location frontend
npm run typecheck
npm test
```

원시 결과 `outputs/aim-sphere-gpu-verification.json`은 각 실행의 사전 검사·장치·계약·실행 상태·fallback·시간을 포함한다. 최종 로그는 `outputs/aim-sphere-final-backend.log`, `outputs/aim-sphere-final-frontend.log`; UI 캡처는 `outputs/aim-sphere-{light,dark,narrow}-{full,cone}.png`에 보관했다. outputs는 Git 비추적 산출물이다.

## 2026-09-11 — main 커밋 요청

사용자 요청에 따라 위 구현·테스트·문서를 `feat: add aim sphere emission for surface emitters` 단위로 main에 기록한다. 원격 main과 기준 커밋이 같은 상태임을 fetch로 확인했다. 로컬 산출물·CAD 파일·별도 렌더링 작업은 커밋 범위에서 제외하며, 서버 재시작이나 EXE/ZIP 배포는 이 요청에 포함하지 않는다. 실행 시험 수치는 2026-09-10 검증 결과이며 커밋 작업에서 재측정한 값이 아니다.
