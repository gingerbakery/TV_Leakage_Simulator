# 근접 Aim Target 검증 및 Tilt 복원 수정

## 요청과 원인

- 가까운 Emitter/Target에서 `Target plane must not intersect the emitter` 오류가 발생한다는 보고를 점검했다.
- 기존 검증은 사각형/원형 Target의 실제 크기가 아니라 **무한히 연장된 평면**을 검사했다. 실제 Target은 Emitter와 떨어져 있어도 연장 평면이 Emitter를 가르면 실패했다. 서로 분리된 CAD 삼각형들이 평면 양쪽에 있어도 일괄 거부했다.
- Target Tilt는 저장된 U/V 방향 축으로 복원한다. 기존 프런트엔드는 별도 normal이 없으면 U/V가 유효해도 `(0,0,0)`을 반환했다. 따라서 방향은 저장되어 있지만 숫자가 0으로 보였고, 다음 축을 수정할 때 다른 축의 각도가 덮어써질 수 있었다.
- 보고된 실패 사례의 `.bitsam`이나 정확한 좌표는 아직 제공되지 않았다. 아래 검증은 원인이 확인된 코드와 합성 재현 장면 기준이며, 사용자 장면의 실제 중첩 여부까지 확인한 것은 아니다.

## 수정 범위

| 파일 | 변경 내용 |
| --- | --- |
| `src/leakage_simulator/aim_sampling.py` | 실제 발광 삼각형별로 유한 사각형/원형 Target과 겹치는지 검사. Target 좌표계로 변환 후 여유 범위를 적용한 클리핑 및 원형 교차 판정 |
| `frontend/src/features/raytracing/ray-tracing-model.ts` | normal 생략 시 저장된 U/V 외적으로 normal을 복원하여 Tilt 계산 |
| `frontend/src/features/raytracing/emitter-aim-editor.tsx` | 복원된 각도 표시의 부동소수점 잔여 자릿수 제거. 소수점 이하 최대 10자리 |
| `frontend/src/features/raytracing/ray-tracing-model.test.ts` | 일반 회전 및 ±90도 회전의 방향 축 복원 테스트 |
| `frontend/src/features/raytracing/emitter-aim.test.ts` | 기울어진 Aim의 `.bitsam` 저장/불러오기 계약 테스트 |
| `frontend/src/features/raytracing/ray-tracing-auto-convergence.test.tsx` | X/Y/Z 순차 입력 → 저장 → 편집창 재열기 및 숫자 문자열 회귀 테스트 |
| `tests/test_emitter_aim.py` | 무한 평면 기준 테스트를 실제 영역 중첩 거부 테스트로 수정 |
| `tests/test_emitter_aim_proximity.py` | 근접/교차/접촉/공면 분리, 다중 CAD 면, 원형 경계, 좌표 변환, scalar/batch 광속 회귀 테스트 9개 |
| `scripts/verify_emitter_aim.py` | CPU/CUDA 비교에 근접 평행/기울어진 유한 Target 장면 추가 |
| `docs/emitter-aim-area.md` | 유한 영역 검증, 수치 여유, Tilt 복원 계약과 한계 갱신 |

## 변경 후 동작과 한계

- **거리가 가깝다는 이유만으로 실패하지 않는다.** 실제 두 영역이 분리되어 있고 수치 여유를 만족하면 허용한다.
- Target은 방향 샘플링을 위한 가상 영역이며 차폐용 물체가 아니다. 실제 영역의 중첩·접촉 및 수치 여유 위반은 계속 거부한다. 공면이지만 떨어진 영역은 target-only 모델에서 허용한다.
- 수치 여유는 기존과 동일한 `max(1e-9, 2 × epsilon_mm)`이다. epsilon이나 전력 보정 상수, 반사율, 광속/lux/nit 환산, CUDA 커널, 난수 및 방향 샘플링 방식은 변경하지 않았다.
- 실제 형상 간격이 epsilon과 비슷하거나 grazing ray가 중요한 장면은 self-hit/교차 공차 수렴을 별도로 확인해야 한다. 아래 극소 간격은 알고리즘 회귀 검증용이며 해당 길이 척도에서 기하광학의 물리적 타당성을 보장하지 않는다.
- Tilt는 저장된 방향 축으로 복원하므로 기존 파일에도 적용된다. Euler 각도는 동등한 표현으로 정규화될 수 있고 ±90도 부근은 gimbal lock이 있다. 임의의 회전 누적 횟수까지 보존하는 계약은 아니다.

## 자동화 및 브라우저 검증

- 관련 Python 테스트 **45개 통과**: Aim 21개, raytrace bridge 17개, RT-3 다회 반사 7개. 백엔드 전체 테스트를 실행한 것은 아니다.
- 프런트엔드 전체 **36개 파일, 241개 테스트 통과**. TypeScript 검사 및 프로덕션 빌드 통과.
- lint 오류 0개. 기존 `result-window.tsx`의 `liveResult` 의존성 경고 1개와 기존 번들 크기 경고 유지.
- 별도 브라우저 테스트 탭에서 TV 우측 코너 STEP을 Import하고 Datum Emitter를 생성했다. Aim Tilt `(20,-30,15)`를 순서대로 입력하고 저장한 뒤 다시 열었을 때 동일한 숫자가 표시됨을 최종 빌드에서 확인했다. 브라우저 오류 로그 없음.
- `.bitsam` round-trip은 자동화 테스트로 확인했다. 브라우저에서는 Emitter 저장/재열기를 확인했으며 파일 다운로드/재업로드까지 수행한 것은 아니다.

## 실제 GPU 검증 환경

- 배포 경로: **소스 checkout**, `C:/Users/Administrator/Documents/TV leakage simulator main`의 `main`, 기준 커밋 `b0732d6ff939cd4b50da481010a58716730807e6` + 본 미커밋 변경.
- 공식 `setup_windows_gpu.bat`의 읽기 전용 사전 점검 및 `run_web_gpu.ps1 -Port 8788` 사용. OS 드라이버/Toolkit 설치, 시스템 설정 변경, 재부팅 없음.
- NVIDIA GeForce RTX 3070, Compute Capability 8.6, 드라이버 591.59, CUDA Toolkit 13.1.1, Python 3.13.3 x64, Numba 0.66.0, Node 24.14.0/npm 11.9.0.
- Production preflight: `available=true`, `strict_float64=true`, `kernel_executed=true`, `kernel_verified=true`, `preflight_scope=production_ray_bvh`, `provider_contract=strict_float64_bvh_v1`, `reason_code=null`.

## CPU/CUDA 결과 및 시간

- 실행: `.venv-gpu/Scripts/python.exe scripts/verify_emitter_aim.py --output outputs/emitter-aim-proximity-20260910.json`.
- Face/Datum rectangle/polygon_auto 3종 × Target rectangle/circle 2종 × 장면 5종 × CPU/GPU 2종 × 최초 + 반복 2회 = **180회 모두 통과**.
- 장면: direct, specular, blocked, close_parallel, close_tilted. 각 8,192 rays, 최대 반사 2회, 저장 경로 20개, 입력 1 lm, 경면 반사율 0.8, 각도 의존 반사 비활성, epsilon `1e-6 mm`.
- close_parallel: Emitter z=0, Target z=`1e-5 mm`, Receiver z=`2e-5 mm`. close_tilted: Emitter z=0, Target 중심 z=0.1 mm, Target normal=+X. Target의 무한 평면은 Emitter를 가르지만 실제 Target은 겹치지 않는다. Target 사각형은 0.04×0.04 mm, 원형은 반경 0.02 mm.
- 근접 두 장면 모두 Receiver 8,192 hits / 1 lm. 기존 장면의 기대 광속은 direct 1 lm, specular 0.8 lm, blocked 0 lm.
- GPU 90회 모두 `compute_execution_state=gpu_active`, reason 없음. CUDA 시도/성공 합계 **90/90**, CPU hybrid success 0, 교차 CPU fallback 0, resident fallback 0.
- 같은 seed의 CPU/GPU 격자 최대 차이 **0 lm**, 이론 총광속과 최대 절대 차이 `1.4210854715202004e-14 lm`.

대표 Face/rectangle 시간(초):

| 장면 | CPU 최초 / 반복1 / 반복2 | GPU 최초 / 반복1 / 반복2 |
| --- | --- | --- |
| direct | 8.37776 / 0.06110 / 0.06233 | 4.15991 / 0.03543 / 0.04752 |
| close_parallel | 0.04662 / 0.04459 / 0.04718 | 0.04055 / 0.03573 / 0.03850 |
| close_tilted | 0.05072 / 0.04517 / 0.05152 | 0.04560 / 0.04970 / 0.04883 |

- 동일 프로세스/세션에서 순차 비교했다. direct 최초에는 JIT 비용이 포함될 수 있고, 뒤의 근접 장면 최초는 cold start가 아니다. 프런트엔드 검사와 일부 병렬 실행했으므로 시간은 기능 검증 참고치이며 성능 개선 주장이나 대규모 CAD 벤치마크가 아니다.
- 합성 사례의 광속 보존 및 CPU/GPU 일치 검증이며 LT 또는 실측 nit 정합 검증은 아니다.

## 적용 상태

- 백엔드 재시작과 프런트엔드 빌드를 완료했다. `http://127.0.0.1:8788/health` HTTP 200 및 `ok api_version=1.0.0` 확인.
- 서버 로그: `outputs/server-8788-20260910-fixed.log`, `outputs/server-8788-20260910-fixed.err.log`.
- 사용자의 기존 작업 탭은 새로고침하지 않았다. 저장 후 새로고침하면 최신 UI가 반영된다.
- 별도 렌더링 작업 폴더는 변경하지 않았다. 최초 구현 요청에서는 커밋/push를 수행하지 않았으며, 이후 사용자가 본 수정분의 커밋과 main push를 요청했다.

## 후속 커밋 준비 및 main 정합 확인

- 원격 main에 추가된 `afb37353` / `4444db69`(원본 STEP 부품 경계 보존)를 확인하고 fast-forward로 적용했다. 변경 파일은 `importers.py`와 STEP 테스트로, 본 Target 수정 파일과 겹치지 않았다.
- `4444db69` 위에서 Aim 테스트 21개와 STEP 부품 경계 테스트 4개를 재실행해 모두 통과했다. 위의 실제 GPU 180회 검증은 앞서 기재한 `b0732d6f` + 본 수정분 기준이며, 이번 커밋 준비 단계에서 다시 실행하지 않았다.
- 커밋 범위는 근접 Target 판정, Tilt 복원, 관련 테스트 및 문서다. 후속 요청인 ROI 선택 경계 렌더링 개선과 Components의 면별 Surface property 아이콘/UI는 포함하지 않는다.
- 커밋 메시지: `fix: validate finite aim targets and restore target tilt`.
