# 최대 반사 횟수 1,000회 확장 및 종료 원인 표시

## 기준과 작업 경계

- 기준 main: `d991858b0626c8bd2f2b713b14d385d82ffab5c1`.
- 소스 전달 경로: Git source checkout, `C:\Users\Administrator\Documents\TV leakage simulator main`.
- 렌더링 브랜치/작업폴더는 수정하거나 병합하지 않았다. 이 작업은 미커밋 상태이며 push하지 않았다.
- 2026-07-27의 상한 20회 결정은 본 변경으로 대체한다. 과거 변경 문서는 이력으로 보존한다.

## 구현 내역과 파일 대응

| 변경 | 파일 |
|---|---|
| 공통 허용 상한 `20 → 1000` | `src/leakage_simulator/types.py` |
| GPU 별도 32회 제한 제거, 공통 상한 사용, 과대 버퍼 할당 전 거부 | `src/leakage_simulator/gpu_cuda_resident_wavefront.py` |
| 깊이를 고려한 배치 크기 제한 및 GPU capacity 반올림 보호 | `src/leakage_simulator/wavefront_event_tape.py` |
| CPU/GPU 배치 제한 적용, 요청/실제 배치 크기와 제한 여부 기록 | `src/leakage_simulator/raytracer.py` |
| UI 입력/저장 복원 상한, 고반사 설정 안내 | `frontend/src/stores/workspace-store.ts`, `frontend/src/features/raytracing/ray-tracing-panel.tsx` |
| 결과창의 상한/관측 깊이/상한 종료/저에너지 이벤트 표시 | `frontend/src/features/results/result-window.tsx` |
| 계약 경계 및 100/1000회 실제 광로, CPU/GPU, 저장 경로, 배치 보호 회귀 검증 | `tests/test_high_reflection_depth.py`, `tests/test_multibounce_rt3.py`, `tests/test_perf4b_gpu_resident_wavefront.py` |
| UI와 `.bitsam` 1000회 저장 복원 검증 | `frontend/src/stores/workspace-store.test.ts`, `frontend/src/features/projects/bitsam-project.test.ts`, `frontend/src/features/results/result-ui.test.tsx` |
| 반복 가능한 광량/성능 검증 스크립트 | `scripts/verify_high_reflection_depth.py` |
| 사용자 해석/비교 방법 | `docs/reflection-depth-convergence-guide.md` |

기본 반사 횟수와 Minimum energy는 변경하지 않았다. 광원, 반사율, Receiver 광량 집계 및 nit 환산/보정 상수도 변경하지 않았다.

## 배치 메모리 보호

- 배치 이벤트 상한: 2,097,152 slots.
- `max_depth=1000`, GPU 기본 요청 65,536 rays/batch → 실제 2,048 rays/batch.
- 전체 Ray 수, seed, ray당 에너지, 최대 반사 횟수를 바꾸지 않고 배치만 분할한다.
- `_performance_summary`: `requested_intersection_batch_size`, `intersection_batch_size`, `wavefront_batch_event_slot_limit`, `wavefront_batch_memory_limited`.
- 깊이 데이터의 `int16` 저장 범위 내에서 1,000회를 지원한다. 반복 루프 기반이므로 Python 재귀 한도를 늘리지 않았다.
- 고차 경로 저장에 따른 UI/파일 용량 증가나 모든 종류의 메모리 부족까지 없애는 변경은 아니다.

## 검증 환경

- GPU: NVIDIA GeForce RTX 3070 / Compute capability 8.6.
- NVIDIA driver 591.59, CUDA Toolkit 13.1.1.
- Python 3.13.3 x64, NumPy 2.4.6, Numba 0.66.0, llvmlite 0.48.0.
- Node.js 24.14.0 / npm 11.9.0.
- 공식 read-only 사전 검사 후 `run_web_gpu.ps1 -PreflightOnly`로 새 main 폴더의 환경/프론트 빌드/실제 production CUDA 검증을 수행했다. 드라이버/Toolkit 설치나 시스템 설정 변경은 하지 않았다.
- 이 도구 실행 세션에서는 `.bat` 래퍼의 CMD 실행 오류가 있어 동일한 공식 `setup_windows_gpu.ps1`로 inventory를 완료했다. 하위 프로세스에 필요한 Windows 경로 환경변수만 제공했다.
- production preflight: `available=true`, `strict_float64=true`, `kernel_executed=true`, `kernel_verified=true`, `preflight_scope=production_ray_bvh`, `provider_contract=strict_float64_bvh_v1`.

## 광량/실행 검증 결과

동일 프로세스/동일 장면에서 각 CPU/GPU 경로의 첫 실행과 warm 2회를 기록했다. 깊이 100/1000은 각각 해당 반사 수가 있어야 Receiver에 도달하는 평행 거울 통로다. 메쉬는 4개 삼각형뿐인 합성 검증 구조이며 실제 TV CAD 성능을 대표하지 않는다.

- Emitter: datum_plane, 총 1 lm, 4,096 rays, 매우 좁은 Gaussian 방출.
- Surface: specular, 반사율 0.999, 입사각 반사율 옵션 off.
- Receiver: 1.5 × 1.5 mm, 8 × 8 cells.
- Minimum energy `1e-15 lm/ray`, epsilon `1e-6 mm`, threshold 종료, 경로 저장 off.
- GPU 모든 측정: `compute_execution_state=gpu_active`, reason 없음, CPU hybrid/fallback 0, resident fallback 0.

| 실제 반사 수 | 계산 | 첫 실행(초) | warm 1(초) | warm 2(초) | Receiver 광속(lm) | CUDA 성공/시도(실행당) |
|---:|---|---:|---:|---:|---:|---:|
| 100 | CPU | 7.4731 | 0.6905 | 0.7025 | 0.904792147114 | 0/0 |
| 100 | GPU | 3.4102 | 0.0494 | 0.0449 | 0.904792147114 | 1/1 |
| 1000 | CPU | 6.9067 | 6.9472 | 6.8972 | 0.367695424771 | 0/0 |
| 1000 | GPU | 0.2582 | 0.1846 | 0.0815 | 0.367695424771 | 2/2 |

- 모든 실행에서 4,096 rays가 Receiver에 도달했다. 표면 hit 수는 각각 409,600 / 4,096,000이다.
- 이론값 `0.999^N` 대비 상대 광량 오차 최대 `2.69e-14`(비율).
- 같은 표본의 CPU/GPU Receiver cell 광속 최대 절대 차이: 0 lm.
- 첫 실행에는 JIT 비용 등이 포함될 수 있다. 1000회 장면은 이미 100회 장면을 실행한 뒤여서 첫 실행도 프로세스 전체의 cold start는 아니다. warm 변동을 포함해 실제 측정값 그대로 기록했으며 일반 CAD 속도나 억 단위 Ray 처리 시간을 보장하지 않는다.
- 1000회가 필요한 통로에서 상한 20/999이면 수광되지 않는 것을 검증했다. 반사율 0.1/에너지 종료 조건에서는 상한 1000이어도 20회 전에 종료되는 것을 검증했다.

## 회귀 및 빌드

- 신규 고반사/메모리 보호 테스트 **9개 통과**(19.382초). 실제 CUDA compact/full event tape, 1,000회 경로 저장, 배치 분할 및 광량 정합성 포함.
- 프론트엔드 **9개 파일 / 80개 테스트 통과**. 입력, `.bitsam` 저장 복원, GPU 실행 UI, 결과 진단 표시 포함.
- TypeScript 검사 및 production build 통과. 기존 큰 번들 경고(500 kB 초과)는 남아 있다.
- `git diff --check` 통과.
- 프론트 테스트 중 jsdom의 Canvas 미구현 경고가 있었으나 테스트 실패는 없다. 실제 브라우저의 시각적 확인을 했다는 의미는 아니다.
- 기존 CPU/GPU/Face/Polygon/API 확대 회귀 **52개 실행 중 기존 테스트 메서드 1개가 실패**했다. `test_perf4c_gpu_summary_accumulator.py::test_low_depth_accumulator_stays_gpu_resident`의 depth 0/1 두 하위 사례다.
- 실패 원인은 의미 동등성 비교에 `config.compute_backend`의 `cpu`/`gpu_cuda` 문자열 차이가 그대로 들어가는 것이다. 광량 숫자 비교는 허용 오차 내에서 통과했다.
- 변경 전 `d991858b`의 `src/scripts/tests`를 별도 임시 디렉터리에 추출하여 같은 테스트를 다시 실행했고, **동일한 두 하위 사례 실패를 재현**했다. 이번 반사 상한 확장의 회귀 실패가 아니며, 관련 없는 테스트 코드는 변경하지 않았다.

## 재검증 명령

GPU 실행 전 `AGENTS.md`와 GPU runbook을 따라 production preflight를 확인한다.

```powershell
.\run_web_gpu.ps1 -PreflightOnly
.\.venv-gpu\Scripts\python.exe -m unittest discover -s tests -p test_high_reflection_depth.py -v
.\.venv-gpu\Scripts\python.exe scripts\verify_high_reflection_depth.py
npm --prefix frontend test -- src/stores/workspace-store.test.ts src/features/projects/bitsam-project.test.ts src/features/results/result-ui.test.tsx src/features/raytracing
npm --prefix frontend run build
```

## 해석상 주의

반사 상한에 의한 누락을 줄일 수 있지만, 이번 합성 검증은 실측/LightTools 정합성 검증을 대신하지 않는다. 같은 장면으로 20/50/100/300/1000을 비교하고 총 광속과 종료 사유를 확인해야 한다. 기존 nit_est 절대 보정, 표면 모델, ROI 경계 조건은 별도 과제다.
