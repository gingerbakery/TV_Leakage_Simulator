# Receiver 경계 정확도 수정: 소스 배포 검증

검증일: 2026-10-22. 대상은 Git 소스 체크아웃이며 ZIP/EXE 배포 검증이 아니다.

## 결론과 배포 범위

Receiver의 **이미 수광된 Ray를 내부 격자 셀에 배정하는 규칙만** 수정했다.
경계에 집중된 Ray가 CPU/CUDA의 극미세 좌표 차이로 양옆 셀에 나뉘는
재현을 해결했다. 교차 계산, 반사 방향, 광량, 난수, 반사 상한과 종료 정책은
변경하지 않았다. 계약과 한계는 [경계 계약](../receiver-binning-contract.md)에 있다.

이번 변경은 새 가속 후보의 승격이 아니다. Gaussian/Mixed MIS 확장,
OptiX, 겹친 면의 정밀 교차 후보와 관련 로컬 실험은 배포에 포함하지 않는다.
기존 main의 가속과 LT 검증 도구, 최신 Receiver 가림/후면 Iso 뷰는 유지한다.

**기존 제품 수송 기준과 Receiver 엄격 비교는 통과했다. 모든 보조 합산까지
32 ULP로 제한하는 추가 진단은 68/80 통과, 12/80 실패를 그대로 유지한다.**
실패는 기존 GPU 반복에서도 나타나는 보조 광량 합산의 끝자리 차이이며,
Receiver 지표와 이산 결과의 실패는 아니다. 전체 엄격 검증 완료 또는
PERF-5 전체 완료로 해석하면 안 된다. 아래 제한을 명시한 좁은 정확도 수정의
사용자 테스트 배포로 판단한다. LT/실측 정합은 이번 검증 대상이 아니다.

## 입력과 환경

- 기준 main: `f685d899001d1b7c5746c78c4e95f5f567d0f050`.
- 검증한 제품/테스트 소스 Git tree: `275c023ea61e2836d76aff2a8f8664a939da4103`.
  이 보고서 추가 전의 tree이며, 배포 시 제품 파일을 이 스냅샷과 대조한다.
- 완전한 tracked 소스를 baseline/candidate-v4로 동결했다. 로컬 미완성 변경을
  포함한 개발 폴더가 아니라 각각의 고정 소스에서 별도 프로세스로 실행했다.
- 소스 manifest SHA-256:
  `4eed3ecaa0663f10714b471f41c03d2c08862e9bbe3d644061999a1d21fafdf6`.
  실행 전후 파일 hash와 실제 import 경로를 확인했다.
- NVIDIA GeForce RTX 3070, compute capability 8.6, driver 591.59,
  CUDA Toolkit 13.1.1, Windows CUDA 13 x64 compatibility layout.
- Python 3.13.3, NumPy 2.4.6, Numba 0.66.0, llvmlite 0.48.0.
- 공식 `run_web_gpu.ps1 -PreflightOnly`로 격리된 새 환경, 고정 의존성,
  프론트엔드 설치/프로덕션 빌드와 실제 CUDA 사전 검사를 수행했다.
  OS/드라이버/Toolkit 설치나 실행 중인 서버 재시작은 하지 않았다.
- `available`, `strict_float64`, `kernel_executed`, `kernel_verified` 모두 true.
  `preflight_scope=production_ray_bvh`,
  `provider_contract=strict_float64_bvh_v1`.

## 정확도와 회귀

### 경계 재현

70도 경사 반사, 16,385 Ray, seed 1000, 반사 상한 2, 최소 에너지 0,
2×2 mm Receiver를 사용했다. 검사용 난수만 고정해 중심을 집중 타격했다.
이는 실제 광원의 분포나 속도 벤치마크가 아니라 경계 스트레스 검사다.

이전 진단의 8×8 격자에서는 GPU/CPU Peak 차이 약 -5.3942%가 나타났다.
최종 코드에서는 Python wavefront, Numba fused CPU, CUDA 모두 8×8의 (4,4),
7×7의 (3,3) 한 셀에만 기록됐다. CPU/CUDA 수광 수는 각각 16,385로 일치했다.

| 격자 | CPU 총광량 (lm) | CUDA 총광량 (lm) | CUDA 성공/시도 |
| --- | --- | --- | --- |
| 8×8 | 0.0017502570909109678 | 0.0017502570909109648 | 1/1 |
| 7×7 | 0.0017502570909109678 | 0.0017502570909109663 | 1/1 |

최대 상대 차이는 약 1.8e-15다. 기존 광량을 보존하면서 경계 셀과 Peak가
일치한다. 기존의 분산된 셀을 합치는 수치 규칙 변경이므로 **이 특수 조건을
다시 해석하면 과거 버전 대비 Peak 자체는 의도적으로 달라질 수 있다.**
이미 저장된 해석 결과를 고쳐 쓰지는 않는다.

독립 Decimal 기준, 격자선 바로 양옆의 표현 가능 값, 경계 밴드 밖,
회전/이동/비스듬한 입사, 다중 Receiver, 외곽 밖 Ray의 거부도 검사했다.
외곽 수광 범위를 넓히거나 광량을 보간/재분배하는 수정이 아니다.

### 통합 회귀와 저장

- Python 33개 모듈, **263 검사 통과**, 실패/오류/skip 0.
- CPU fused/wavefront, 실제 CAD, CUDA resident/pair BVH/집계,
  Face/polygon emitter, Aim Sphere/근접 Target/양면 발광 포함.
- 반사율·반사 분포·광량·종료·좌표/면 속성의 기존 합성 회귀 포함.
- `.bitsam` 저장/복원, 원본 CAD 재파싱 없이 내장 형상 재사용,
  저장 결과 보존, CPU/GPU 재해석, LT 비교 도구의 API 연동 포함.
- 실제 GPU analytic/BSDF 저장·복원 후 재해석과 광원 광량 50% 변경에
  따른 결과 50% 변화 확인. LT 프로그램을 직접 실행한 검증은 아니다.

## 기존 버전과 실제 모델 비교

합성 7조건(직접 Lambertian/isotropic/Gaussian, 반사 Lambertian/Gaussian/
혼합/specular)과 사용자 저장 모델 BEFORE/AFTER/CASE2를 비교했다.
각 소스/모드/조건에서 첫 실행과 warm 3회를 같은 세션에 기록했다.
기준/수정 버전 총 160실행, 같은 backend의 전후 비교 80개다.

합성은 16,385 Ray, seed 3179. 실제 모델은 datum-plane emitter 1개,
131,073 Ray, seed 1000, 반사 상한 100, primary/bounce Source sampling이다.
원본 `.bitsam`은 읽기만 했으며 원본 및 결과 파일 hash를 검증했다.
저장 파일의 원래 수천만 Ray 조건을 전부 재실행한 것은 아니다.

| 항목 | 결과 |
| --- | --- |
| 이산 결과(수광 Ray 수·분기 등) 전후 일치 | 80/80 |
| 기존 제품 수송 기준 `transport_acceptance_gate` | 80/80 |
| Receiver 지표·격자 전후 엄격 비교 | 80/80, 최대 1 ULP |
| 수정 버전 Receiver 반복 엄격 비교 | 80/80 |
| 전체 보조 집계 포함 32 ULP 추가 진단 | 68/80; GPU 실제 모델 12개 실패 유지 |
| 기존 버전의 독립 warm 반복 전체 32 ULP 진단 | 60개 중 GPU 실제 모델 9개 실패 |

제품 기준은 기존 `scripts/perf5d_evidence.py`의 고정 기준을 사용했다.
모든 이산 값은 exact, 전체 광량은 기존 abs/rel 1e-9 기준,
Receiver는 abs/rel 1e-12 AND 최대 32 ULP 기준이다. 허용치를 변경하지 않았다.
추가 32 ULP 전체 진단은 별도 실패 보고서로 남겼고 통과로 바꾸지 않았다.

실패 필드는 반사 깊이/종류별 continued/escaped flux와 표면별 incoming/
potential reflected flux 보조 합산에 한정됐다. 전후 전체 최대 절대 차이
9.7203e-12 lm, 상대 차이 2.6567e-13(약 2.66e-11%)이다. 기존 버전끼리
반복해도 최대 상대 3.1403e-13이 나타났다. GPU 병렬 합산 순서의 끝자리
차이와 Receiver 경계에서 발생했던 5.39% Peak 차이를 구분해야 한다.

GPU 80실행은 모두 `gpu_active`, reason null, CUDA 성공=시도 >0,
CPU hybrid/교차 fallback/resident fallback/planner·reducer fallback 0이다.
조건별 실행당 CUDA 성공/시도는 합성 1/1, 실제 모델 3/3이었다.
일반 resident BVH와 pair BVH를 모두 검사했으며 pair 전용 실행 조건은
실제로 pair가 선택된 경우에만 추가 검사했다. CPU 80실행은 CPU로 보고했다.

### 참고 시간 (초)

| 모델 | 모드 | 기준 warm 중앙값 | 수정 warm 중앙값 |
| --- | --- | ---: | ---: |
| BEFORE | CPU | 0.824656 | 0.827029 |
| BEFORE | GPU | 0.502236 | 0.508723 |
| AFTER | CPU | 0.794427 | 0.794030 |
| AFTER | GPU | 0.469653 | 0.472968 |
| CASE2 | CPU | 0.698296 | 0.684987 |
| CASE2 | GPU | 0.304036 | 0.294961 |

실제 모델 warm 변화는 약 -2.98%~+1.29%였다. 짧은 합성 GPU 직접
Lambertian은 16.1→24.7 ms로 느려졌으므로 모든 조건 무회귀라고 하지 않는다.
첫 실행은 JIT/업로드 비용을 포함하며 원본 보고서에 별도 기록했다.
순차 실행한 소규모 회귀의 참고 시간이지 새 가속률 또는 50M 성능 보장은 아니다.

## 배포 검사

- 최신 tracked 프론트엔드의 TypeScript 검사 + Vite 프로덕션 빌드 통과.
- UI **57파일/494 검사 통과**. jsdom Canvas 미구현 경고와 기존 대형
  번들 경고는 있었으나 테스트/빌드는 성공했다. 실제 브라우저 픽셀 검사는 아니다.
- 빌드에 사용한 프론트엔드 소스 193파일이 최종 스냅샷과 동일함을 확인했다.
- 최종 backend와 실제 빌드 산출물을 연결한 격리 TestClient에서 `/`,
  JS/CSS 자산, `/health`, `/api/docs`가 200을 반환했다.
  실행 중인 사용자 서버와 메모리 CAD 세션은 건드리지 않았다.
- 사용자 환경 업데이트는 Git pull 후 공식 `run_web_gpu.bat`를 다시 실행해
  frontend를 재빌드하고 서버를 갱신해야 한다. 기존 EXE/ZIP은 별개다.

## 실패 이력과 제한

초기 후보의 CUDA 컴파일 서명 오류 및 CPU/CUDA 혼합 컴파일 순서 오류를
실제 장치 검사가 검출했다. 명시적 분기, Python float 상수와 `math.floor`
형식으로 수정하고 최종 고정 스냅샷에서 263개를 처음부터 다시 통과시켰다.
실패/중단된 이전 실행은 최종 통과 개수에 넣지 않았다.

배포 검사 도구의 잘못된 `/api/health` 가정은 실제 경로 `/health`로
고쳤다. 모든 장면에 pair-BVH 전용 gate를 적용한 진단 도구 오류도
일반 resident 경로와 구분해 수정했다. 제품 수치 gate는 변경하지 않았고
최초 전체 strict 실패 JSON과 이전 진단 결과를 보존했다.

독립 reviewer는 소스/테스트의 정적 검토에서 blocking P1/P2를 찾지 않았다.
GPU 실행 자체는 메인 담당이 수행했으며 정적 검토를 실행 증거로 대신하지 않았다.
이 검증은 모든 모델·광원·깊은 반사에서의 물리적 정확성 증명이나 clean-PC,
다중 GPU/driver, 장시간 50M/100M 시험을 대체하지 않는다. 겹친 면 선택의
남은 정밀 후보와 LT/실측 정합은 별도 과제로 유지한다.

## 로컬 증거

원본 CAD와 대용량 결과는 Git에 넣지 않는다. 로컬 경로:
`outputs/validation/2026-10-22-receiver-release-v1/`.

- `manifest-v4.json`, `regression-v4.json`, `regression-v4.log`.
- `baseline-comparison/report.json`, `candidate-v4-comparison/report.json`:
  160개 압축 결과의 파일 hash, 실행 상태/카운터, 첫 실행 및 warm 시간.
- `comparison-audit.json`: 전체 strict 실패를 유지한 원본.
- `comparison-diagnostic-audit-v2.json`: 기존 제품 기준과 baseline 반복을
  별도 판정한 결과. 최초 strict 결과를 덮어쓰지 않았음을 hash로 확인한다.
- `frontend-test.log`, `launcher-preflight.log`, `deployment.json`.

이 보고서는 검증 시점의 소스 배포 범위를 기록한다. 최종 커밋 및 원격
반영 여부는 Git 이력으로 확인한다.
