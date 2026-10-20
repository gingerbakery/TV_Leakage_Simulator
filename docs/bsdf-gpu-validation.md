# BRDF / BSDF 입력과 NVIDIA GPU 계산

2026-10-14 · 소스 체크아웃 `codex/bsdf-import-foundation` · 기반 커밋 `157230c6` + 작업본.

Material / Surface Property에서 연결한 LT ASCII 산란 표와 RT 파일이 이제
**NVIDIA CUDA의 실제 반사 계산에 적용된다.** CPU·GPU 자체 검증은 통과했다.
**LightTools 실행 결과와의 직접 교차 검증은 아직 하지 않았다.** 아래 상관계수는
독립 수치 적분과 비교한 값이며 LT 매칭률이나 실제 소재 정확도를 뜻하지 않는다.

## 적용 방법

1. 이 변경이 포함된 소스 체크아웃에서 `run_web_gpu.bat`을 실행한다.
   NVIDIA GPU·드라이버·CUDA Toolkit 13.1 및 production FP64 CUDA 사전 점검이 필요하다.
   기존에 풀어 둔 EXE/ZIP은 이 소스 변경으로 갱신되지 않는다.
2. Material Assignment 또는 Surface Property의 **LT ASCII 파일 등록**에서
   master, 산란 표, RT 파일을 함께 선택한다. 파일은 각각 분리된 상태로 입력한다.
3. 해당 BSDF를 연결하고 **등방성 · No Shift로 계산**을 켠 뒤 계산 파장을 선택한다.
   기존 Apply/Save로 부품 또는 면에 적용한다.
4. Ray Tracing의 연산 장치를 **NVIDIA GPU**로 선택한다. 가속 구조는 Auto 또는 BVH,
   sampling은 Source로 둔다. 오래된 프로젝트는 CPU로 열릴 수 있으므로 장치를 확인한다.
5. 실행 결과의 Compute 상태와 BSDF 적용 정보를 확인한다. `gpu_active` 또는
   `gpu_mixed`와 CUDA 성공 횟수가 GPU 사용 근거다. BVH/Rebuilt 표시는 근거가 아니다.

미지원 옵션을 기존 Mixed/Gaussian으로 대체하여 계산하지 않는다.
연결한 BSDF의 RT 반사율이 기존 물성의 반사율을 대체하며, 기존 Specular/Diffuse 비율이나
Gaussian 각도를 중복 적용하지 않는다. BSDF 연결 해제 시 기존 물성으로 돌아간다.

## 지원 범위

| 항목 | 구현 상태 |
| --- | --- |
| 파일 | LT ASCII AngGridMultiple master + Polar 2D 상대 BSDF + DFAT RT |
| 계산 | 반사 전용, 등방성, No Shift, 공기 중, 양면에 같은 표 |
| 표 범위 | 출사 반구 전체 0–90° / 0–360° |
| 보간 | 입사각·선택 파장, 정규화된 표의 혼합 + RT 별도 보간 |
| GPU | 반사율 계산, 표/셀 선택, 셀 내부 산란 추출, 다중 반사 |
| 기록 | BSDF 기여도, 광선 경로, 재질 연결·원본·파장·연산 장치 저장 |
| 미지원 | 투과, 이방성, Shift/Stretch, 네이티브 단일 .bsdf, BSDF receiver MIS |

전체 LT BSDF 형식을 지원한다는 의미는 아니다. 세부 형식과 제한은
[파일 형식 문서](bsdf-import-foundation.md), LT 비교 순서는
[계산 및 LT 검증 절차](bsdf-transport-validation.md)를 따른다.

## 계산 방식

파일 파싱·입력 검증·방향 분포의 누적 확률표 준비는 CPU에서 한다.
동일 자산은 면마다 복제하지 않고 묶어서 GPU에 올린다. 광선이 표면에 닿으면
GPU가 실제 입사각의 RT 반사율과 산란 방향을 계산한다.

```text
반사 광속 = 입사 광속 × R_RT
방향 확률밀도 = 상대 BRDF 표 × cos(출사각) / 반구 적분값
```

표 안에서는 bilinear 보간을 유지하고, 셀 안의 방향도 같은 확률분포로 추출한다.
재추출 횟수를 임의로 제한하거나 실패한 방향을 정반사로 바꾸지 않는다.
strict FP64로 계산하며 fastmath/float32 근사로 대체하지 않는다.
CPU 배치 경로와 GPU는 같은 counter RNG를 사용한다. 이전 독립 scalar CPU 경로와는
난수열이 다르므로 과거 결과의 각 bin이 그대로 재현되는 것은 아니다.

작은 광선 묶음이나 일부 실행 조건은 기존 hybrid CPU 경로를 이용할 수 있다.
그 경우 `gpu_mixed`로 표시한다. BSDF 반사가 CPU에서 실행된 경우에도 GPU 교차 판정만을
근거로 전체 BSDF GPU 성공이라고 표시하지 않도록 실행 상태를 보완했다.
CAD 가져오기, 파일 해석, UI 전체가 GPU에서 실행된다는 의미는 아니다.

## 실제 GPU 자체 검증

시험 장비: **NVIDIA GeForce RTX 3070**, compute capability 8.6,
드라이버 591.59, CUDA Toolkit 13.1.1, Python 3.13.3 x64.
소스 실행 경로의 production 사전 점검 결과:

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

합성 표 두 종류로 6개 조건을 각 100,000 rays씩 실행했다.
표준 편평 반사면, RT=30%, 광원=1 lm이며 SciPy 기반의 별도 수치 적분과 비교했다.
반사 총량은 0.3 lm, 아래 수광량은 유한한 리시버 안으로 들어온 광량이다.

| 조건 | 수광량 편차 | 상관계수 r | 정규화 분포 겹침 |
| --- | ---: | ---: | ---: |
| Lambertian 0° | −0.0733% | 0.999820 | 98.4144% |
| 방향성 0° | −0.0235% | 0.999983 | 99.1762% |
| 방향성 30° | −0.0111% | 0.999969 | 98.9764% |
| 방향성 60° | −0.0554% | 0.999896 | 98.6409% |
| 방향성 80° | +0.1827% | 0.999579 | 98.2248% |
| 방향성 45° 보간 | +0.0262% | 0.999900 | 98.7136% |

판정 기준은 수광량 편차 ≤1%, r≥0.99, 겹침 ≥95%이며 모두 통과했다.
같은 seed의 CPU/GPU 결과는 이 여섯 조건에서 400개 수광 bin과 비교 대상 결과가
정확히 일치했다. 기준 적분 해상도를 두 배로 변경한 최대 L1 차이는 0.00877%였다.

추가 8개 조건: 반사율 0/100%, 다중 반사와 Russian roulette, 상세 기여도,
두 BSDF 재질, 실제 면 광원, 파장·입사각 보간, GPU chunk 크기 변경도 통과했다.
모든 조건은 `gpu_active`, CUDA 및 resident BSDF 실행 성공, CPU hybrid/fallback 0을 확인했다.
기본 여섯 조건은 CUDA 시도/성공 각각 2/2, resident 성공 2회다.
chunk 변경 조건은 각각 7/7 및 resident 성공 7회다.

추가 조건에서도 이산 판정은 모두 같았다. 실수는 절대 또는 상대 10⁻¹² 이내였다.
다중 반사 누적값의 최대 절대 차이는 약 4.83×10⁻¹³ lm였다.
**별도로 남긴 8 ULP 진단은 다중 반사 3조건에서 통과하지 않았다.**
0 부근 좌표의 소거 오차와 GPU 누적 순서 차이이며, 단위가 있는 절대/상대 오차와
이산 판정 기준으로는 통과했다. 전체 결과가 bit-exact 또는 8 ULP 이내라고 주장하지 않는다.
공용 정확도 판정 도구는 변경하지 않았고 원래 진단을 `report.json`에 함께 보존했다.

같은 세션의 첫 실행 + warm 2회도 기록했다. 최초 CUDA 조건은
4.267 / 0.073 / 0.068초로 첫 실행에 JIT 비용이 포함된다.
방향성 60°는 0.086 / 0.110 / 0.069초, 동일 CPU 배치 조건은 1.907초였다.
이 작은 단일면 시험의 시간은 TV 조립체 전체의 속도 보증이 아니다.

자동 검사 85개 및 프런트엔드 459개가 통과했고 production 화면 빌드도 완료했다.
표준 GPU 정확도·resident·summary accumulator·compact workspace 검증도 통과했다.
공용 host-roundtrip 시험의 작은 후속 광선 묶음은 예상대로 `gpu_mixed`이며,
그 중 receiver hit가 1인 조건은 분포 품질의 근거로 사용하지 않았다.
여기서 분포 검증에 사용한 BSDF 여섯 조건은 모두 충분한 수광 표본을 가진다.

실제 Chrome 화면에서도 60° 시험 프로젝트를 GPU로 실행하고 Save의 다운로드 경로로
저장한 뒤 새로 불러와 재실행했다. BSDF 원본·재질 연결·GPU 선택과 저장된 결과가
복원됐고, 400개 수광 bin이 정확히 일치했다. 전후 수광량은 **0.294009 lm**이다.
네이티브 운영체제 저장 대화상자는 이 브라우저 자동화 검사의 범위에 포함하지 않았다.
`ui-validation.json`, `ui-result.png`에 결과와 실제 장치 증거를 보존했다.

## 다시 검증하기

```powershell
.\run_web_gpu.ps1 -PreflightOnly
.\.venv-gpu\Scripts\python.exe tools\validate_bsdf_gpu.py
.\.venv-gpu\Scripts\python.exe tools\build_bsdf_validation_projects.py --directory outputs\bsdf-gpu-validation --backend gpu_cuda
```

결과는 `outputs/bsdf-gpu-validation/report.html`과 `report.json`에 저장된다.
JSON에는 실제 실행 장치, CUDA 시도·성공, fallback, 시간, 광원 종류,
소스 SHA-256, CPU/GPU 비교와 독립 적분 비교를 기록한다.
`outputs/bsdf-gpu-calibration-20261014.zip`은 회사에서 비교할 수 있는 합성 시험 묶음이다.
기존 CPU 시험 묶음과 별도로 생성한다. 여섯 .bitsam은 GPU로 설정되어 있으며
STEP·원본 산란 표·RT·GPU 결과 CSV·사내 비교 스크립트가 포함된다.

LT에서 같은 표·RT·No Shift로 실행한 뒤 smoothing 없는 flux CSV를 현지에서 비교하면 된다.
GPU 묶음의 `*-sim.csv`는 이번 GPU 결과다. LT 파일을 외부로 가져올 필요가 없다.

```powershell
python compare_bsdf_lt_csv.py --sim-directory . --lt-directory "LT_CSV_폴더"
```

다음 검증 순서는 Lambertian 0° → 방향성 60° → 나머지 각도와 45° 보간 → 실제 조립체다.
회사 LT 검증 완료 전에는 현재 수치를 LT 정합성 검증 완료로 해석하지 않는다.
