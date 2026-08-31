# NVIDIA CUDA GPU 실행·검증 가이드

GPU 사용자는 먼저 전달 방식을 구분해야 한다. Git source와 GPU ZIP은 서로
다른 실행 환경이며, 한쪽을 갱신해도 다른 쪽은 바뀌지 않는다.

| 받은 것 | 실행 파일 | 환경 준비 | GPU 통과 기준 |
| --- | --- | --- | --- |
| Git source/branch | `run_web_gpu.bat` | Python·frontend 자동 동기화 | 서버 시작 전 `[GPU VERIFIED]` |
| GPU CUDA ZIP | `CHECK_GPU_CUDA.bat`, 이후 `LeakageSimulator.exe` | Python·frontend 포함 | checker의 `[OK]` |
| Lite ZIP | `LeakageSimulator.exe` | CPU runtime 포함 | GPU 사용 불가 |

> `git pull`은 이미 압축 해제한 EXE, ZIP, `_tools`, `.venv`를 업데이트하지
> 않는다. 반대로 새 GPU ZIP을 받아도 source checkout은 바뀌지 않는다.

## A. Git source에서 실행

### A-1. PC 준비

처음 설치하거나 어떤 사전 프로그램이 필요한지 확실하지 않으면
[`WINDOWS_GPU_SETUP.md`](WINDOWS_GPU_SETUP.md)의 읽기 전용 점검부터 실행한다.
`setup_windows_gpu.bat`은 기본적으로 설치하지 않고 현재 상태만 보여주며,
명시적으로 승인한 `-Install` 모드에서만 누락된 고정 package를 설치한다.

- [ ] 64-bit Windows와 CUDA 지원 NVIDIA GPU
- [ ] GPU와 CUDA Toolkit `13.1`이 호환되는 NVIDIA display driver
- [ ] CUDA Toolkit `13.1`
- [ ] Python `3.13` 64-bit (`py` launcher 또는 PATH의 `python.exe`)
- [ ] Node.js LTS와 `npm`

### A-2. pull 후 원클릭 실행

프로젝트 루트에서 다음 파일을 더블클릭한다.

```text
run_web_gpu.bat
```

또는 PowerShell에서 포트를 지정한다.

```powershell
.\run_web_gpu.bat 8788
```

이 launcher는 매번 다음 순서로 검증한다.

1. 전용 `.venv-gpu`가 Python 3.13 64-bit인지 확인한다.
2. `requirements-dev.txt`와 `requirements-gpu-cuda.txt` hash가 바뀌면
   `.venv-gpu`를 새로 만든다.
3. 두 requirements를 다시 동기화하고 `pip check`와 exact-pin 검사를 한다.
4. `npm ci`로 `package-lock.json`과 frontend package를 일치시킨다.
5. 최신 frontend production build를 만든다.
6. 실제 production BVH 장면을 GPU에 올리고 hit/miss Ray 결과를 FP64로 검사한다.
7. 모두 성공한 경우에만 서버와 브라우저를 연다.

정상 시작에는 다음 표시가 모두 있어야 한다.

```text
[PYTHON VERIFIED] requirements-dev.txt + requirements-gpu-cuda.txt are synchronized.
[GPU VERIFIED] Device: <NVIDIA GPU 이름>
[GPU VERIFIED] Real Ray/BVH CUDA kernel: PASS | scope production_ray_bvh
[GPU VERIFIED] The production Ray/BVH CUDA kernel passed. The server is now starting.
```

`[GPU FAILED]` 또는 `[GPU SOURCE FAILED]`이면 GPU 서버가 시작되지 않는다.
가장 가까운 `[ACTION]` 문구를 처리하고 다시 실행한다. CPU 사용이 목적일 때만
별도 `run_web.bat`을 사용한다.

## B. GPU CUDA ZIP에서 실행

### B-1. PC와 파일 확인

NVIDIA driver와 CUDA Toolkit 설치 절차는
[`WINDOWS_GPU_SETUP.md`](WINDOWS_GPU_SETUP.md)를 따른다. GPU ZIP에는 Python과
Node.js가 포함되므로 이 두 항목은 설치하지 않는다.

- [ ] 64-bit Windows와 CUDA 지원 NVIDIA GPU
- [ ] GPU와 CUDA Toolkit `13.1`이 호환되는 NVIDIA display driver
- [ ] CUDA Toolkit `13.1`
- [ ] 파일 이름이 `leakage_simulator_desktop_*_gpu_cuda.zip`
- [ ] 함께 받은 `.sha256`과 `.handoff.json`의 commit·hash 확인

GPU ZIP에는 Python, Numba `0.66.0`, llvmlite `0.48.0`, frontend build가 들어
있다. Python, Node.js, npm은 따로 설치하지 않는다. NVIDIA driver와 CUDA
Toolkit은 ZIP에 포함되지 않는다.

### B-2. 압축 해제

1. ZIP을 `C:\TV_leakage_simulator_gpu` 같은 짧은 새 폴더에 전체 압축 해제한다.
2. ZIP 내부에서 EXE를 직접 실행하지 않는다.
3. 이전 GPU/Lite 폴더 위에 덮어쓰지 않는다.
4. `_tools`, `src`, `frontend`, `scripts`를 이동하거나 삭제하지 않는다.

### B-3. 이 PC에서 실제 GPU 검사

1. `CHECK_GPU_CUDA.bat`을 더블클릭한다.
2. GPU 이름과 `Real Ray/BVH CUDA kernel: PASS`를 확인한다.
3. 마지막의 다음 문구를 확인한다.

```text
[OK] NVIDIA CUDA runtime and the production Ray/BVH kernel are working on THIS PC.
```

`gpu_cuda_runtime_manifest.json`은 패키지를 만든 PC의 기록이다. 현재 PC의
동작은 반드시 `CHECK_GPU_CUDA.bat`으로 다시 증명한다.

## C. 앱에서 GPU 선택

1. Source는 열린 브라우저, ZIP은 `LeakageSimulator.exe`를 사용한다.
2. CAD와 `.bitsam` 프로젝트를 연다.
3. `Ray Tracing` 탭 맨 위의 `연산 장치` 영역을 확인한다.
4. `NVIDIA GPU` 버튼을 선택한다.
5. `준비 완료 · <NVIDIA GPU 이름>` 한 줄 상태를 확인한다.
6. `Run Ray Tracing`을 누른다.

`Acceleration structure` 또는 기존 명칭 `Intersection backend`의 `BVH`는
GPU 장치 선택이 아니다. 이 전문 설정은 `Run Options > 고급 옵션` 안에 있으며
일반 사용자는 `자동 최적화 (권장)`를 유지한다. GPU 선택 값은
`compute_backend=gpu_cuda`이고, 앱이 호환되는 BVH 경로를 자동 적용한다.

## D. 실행 결과로 실제 GPU 사용 증명

사전 검사는 GPU가 실행 가능한지를 증명하고, 결과의 `Compute` 행은 해당 ray
tracing run이 실제 GPU를 사용했는지를 증명한다.

| 결과 표시 | 판정 |
| --- | --- |
| `Compute device · GPU 활성` + GPU 이름 | 이 run에서 GPU provider 사용 |
| `Compute device · GPU 활성 · CPU 보조` | GPU와 작은 wave용 CPU를 함께 사용 |
| `CUDA batches · 성공/시도`에서 성공 수 > 0 | 실제 CUDA batch 실행 성공 |
| `CPU small waves` 배지 | 정상 hybrid 처리일 수 있음 |
| `CPU 대체 실행 · GPU 미사용` 또는 `CUDA batches · 0/...` | GPU 요청은 했지만 이 run은 CPU로 실행 |
| `Compute device · CPU 실행` | CPU 모드로 실행 |

GPU 테스트 보고에는 다음 네 가지를 같이 남긴다.

- `run_web_gpu.bat` 또는 `CHECK_GPU_CUDA.bat`의 GPU 이름·kernel PASS
- 결과의 전체 `Compute` 행
- 같은 장면의 첫 실행과 2·3번째 실행 시간
- emitter 종류와 사용한 `.bitsam`

결과에 `Compute` 행 자체가 없으면 최신 frontend가 아니다. Source에서는
`run_web_gpu.bat`을 다시 실행하고, EXE에서는 새 GPU ZIP을 새 폴더에 푼다.

현재 결과에는 `CPU/GPU 동일 샘플 계약` 배지도 함께 확인한다. GPU 활성인데 이
배지가 없으면 서로 다른 Monte Carlo stream을 사용하던 이전 결과일 수 있으므로
현재 버전에서 다시 실행한다.

Source 환경에서 장치 정합을 독립 검증하려면 다음을 실행한다.

```powershell
.\.venv-gpu\Scripts\python.exe scripts\verify_gpu_cpu_accuracy.py --rays 100000
```

마지막 JSON의 `passed=true`, 각 case의 `semantic_exact=true`,
`gpu_execution_proven=true`를 모두 요구한다. 이 검증은 CPU/GPU 구현 정합성용이며
실측 nit의 물리 정확도 보정은 별도다.

위 명령은 정확한 기준 보존을 위해 PERF-4A `host_roundtrip` 경로를 검사한다.
프로덕션 GPU의 PERF-4B resident 경로는 다음 명령으로 추가 검증한다.

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4b_resident_wavefront.py `
  --rays 100000 --repeats 3
```

`passed=true`, 모든 case의 `parity.passed=true`,
`resident_evidence.resident_success_count > 0`,
`resident_evidence.resident_fallback_count=0`을 요구한다. 확률 반사의 CUDA
초월함수는 CPU와 수 ULP 차이가 날 수 있으므로 이산 결과 exact와 strict float64
tolerance를 함께 판정한다.

프로덕션 summary 경로의 PERF-4C GPU 누적기는 다음 명령으로 검증한다.

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4c_gpu_accumulator.py `
  --rays 100000 --repeats 3
```

모든 case의 `passed=true`, `discrete_exact=true`,
`float64_tolerance_passed=true`, accumulator success 1 이상, resident fallback
0과 `strict_float64_gpu_summary_accumulator_v1` 계약을 요구한다. CUDA atomic
합산 순서 때문에 float가 bit-exact하지 않을 수 있으므로 `semantic_exact=false`
하나만으로 실패 판정하지 않는다. 이산 결과 exact와 strict `1e-9` 물리량 허용
오차를 함께 확인한다. `gpu_accumulator=host`는 4B 비교 진단용이며 일반 사용자는
기본 `auto`를 유지한다.

PERF-4D compact workspace 검증:

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4d_compact_workspace.py `
  --rays 100000 --repeats 3
```

이산 exact, strict float64, resident fallback 0,
`compact_summary_sparse_path_retrace_v1`, full 대비 workspace byte 감소를 모두
확인한다. workspace가 줄어도 wall time이 줄지 않았다면 속도 향상으로 표현하지
않는다.

PERF-4E primary Receiver MIS 검증:

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4e_receiver_mis.py `
  --rays 20000 --repeats 12
```

CPU/GPU 이산 exact, strict float64와 finite MIS weight를 확인한다. UI의
`Receiver-directed MIS`는 Lambertian/isotropic batch Emitter에만 적용되고
Gaussian·scalar-only Emitter는 source sampling으로 돌아간다. 직접 보이는
synthetic Receiver의 분산 감소를 차폐된 실제 TV 성능 보장으로 해석하지 않는다.

Auto convergence는 `independent_segment_weighted_v1` 계약으로 독립 구간을
누적한다. `1→2→4→8배`는 `1+1+2+4=8배` Ray를 처리한다. 저장 결과의
`_convergence_accumulation.segment_rays`, config/Emitter seed와 compute state를
함께 확인한다.

PERF-4E-B Lambertian bounce MIS 검증:

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4e_bounce_mis.py `
  --rays 20000 --repeats 12 --parity-rays 8192
```

`bounce_sampling_strategy=receiver_mis`는 순수 Lambertian 반사점에서만
Receiver 면적 proposal을 원래 cosine 분포와 혼합한다. 반사 Ray 자체가 기존
CUDA BVH를 통과하므로 중간 차폐물이 있으면 Receiver보다 먼저 차폐면에
도달한다. Specular는 기존 delta 경로를 유지하고 Gaussian·Mixed는 source
sampling으로 fallback한다. 결과의 다음 필드를 함께 확인한다.

- `bounce_sampling_strategy`
- `bounce_sampling_receiver_directed_fraction`
- `bounce_sampling_unsupported_surface_count`
- `bounce_sampling_fallback_reasons`
- `bounce_sampling_weight_min/max`
- `bounce_sampling_effective_sample_ratio`

## E. 기존 프로젝트와 지원 범위

- `compute_backend`가 없는 기존 `.bitsam`은 CPU로 열린다.
- GPU 테스트 전 프로젝트마다 `연산 장치 > NVIDIA GPU` 선택을 확인한다.
- GPU 선택을 저장한 프로젝트는 다음 실행에서 해당 선택을 복원할 수 있다.
- Face emitter의 primary ray는 batch 생성 후 CUDA BVH로 교차 판정한다. 작은
  Face primary batch도 CUDA를 직접 호출한다. 다만 이후 반사 wave가 8,192개
  미만이면 CPU hybrid가 포함될 수 있다.
- `polygon_auto` emitter는 면적 가중 삼각형 샘플링으로 virtual-plane batch
  경로를 사용하며 CUDA BVH와 호환된다. 작은 batch는 사각 Datum Plane과
  동일한 hybrid CPU 임계값이 적용될 수 있으므로 결과 `Compute` 행의 GPU
  batch와 fallback을 기준으로 실제 실행 장치를 판정한다.
- 작은 장면은 JIT와 전송 비용 때문에 CPU보다 빠르지 않을 수 있다.
- 일반 GPU summary 실행은 PERF-4C가 Receiver/Heatmap/기여도를 device에서 누적하고
  compact summary와 선택된 표시 path만 내려받는다. 상세 contribution 진단은
  full event tape 경로를 사용할 수 있다.
- CAD import, BVH build, UI 전체가 GPU 가속 대상은 아니다.
- 성능 비교는 같은 앱 세션에서 같은 장면을 2·3회 실행해 warm 결과를 기록한다.
- Receiver 결과의 `Heatmap · Sparse/Noisy`는 GPU 오류가 아니라 셀별 표본 부족일
  수 있다. CPU/GPU 동일 샘플 계약이 확인된 상태에서도 이 표시는 별도로 해소해야
  한다.
- Auto convergence `1→2→4→8배`는 독립 구간 `1+1+2+4`를 누적해 총 `8배`
  Ray를 처리한다. Receiver 또는 grid 계약이 실행 중 바뀌면 누적을 중단한다.

### 성능 숫자 해석

기존 보고서의 `7.28초 → 5.54초(-23.9%)`는 이전 PERF-3C GPU build와
PERF-3D GPU build의 비교다. CPU와 GPU를 직접 비교한 수치가 아니다.

| 상황 | 기대 |
| --- | --- |
| branch pull 후 CPU 선택 | GPU 가속 없음; 자동으로 빨라진다고 판정하지 않음 |
| 첫 GPU run | BVH build·CUDA JIT가 포함돼 느릴 수 있음 |
| 같은 장면의 warm GPU run | GPU batch가 실제 성공했을 때 비교 가능 |
| CAD import·BVH build만 비교 | GPU ray-tracing 속도 증거가 아님 |

## F. 문제 해결

Driver, CUDA Toolkit, Python 또는 Node.js 자체가 없거나 버전이 맞지 않으면
[`WINDOWS_GPU_SETUP.md`](WINDOWS_GPU_SETUP.md)의 RTX A4000/일반 Windows 설치
절차와 사내 AI용 프롬프트를 사용한다. 사내 정책이나 UAC를 우회하지 않는다.

| 표시 | 조치 |
| --- | --- |
| `cuda_driver_unavailable` | 호환 NVIDIA driver 설치 → 재부팅 → 재검사 |
| `cuda_toolkit_not_found` | CUDA Toolkit `13.1` 기본 설치 또는 `CUDA_PATH` 확인 |
| `numba_not_installed` | Source는 `run_web_gpu.bat`, EXE는 GPU ZIP 사용 |
| dependency pin mismatch | `run_web_gpu.bat` 재실행; 계속되면 `.venv-gpu` 삭제 후 재실행 |
| frontend/npm 실패 | Node.js LTS 확인 후 `run_web_gpu.bat` 재실행 |
| 앱이 열리지 않음 | ZIP은 `desktop_runtime\launcher.log`; Source는 launcher 창의 첫 오류 확인 |
| Compute가 CPU | GPU 선택·emitter·fallback reason·GPU batch count 확인 |

CUDA Toolkit 폴더에는 다음 파일이 있어야 한다.

```text
bin\x64\cudart64_*.dll
nvvm\bin\x64\nvvm*.dll
nvvm\libdevice\libdevice*.bc
```

## G. 빠른 완료 기준

| 단계 | Source | GPU ZIP |
| --- | --- | --- |
| 전달 확인 | branch/commit | ZIP + `.sha256` + `.handoff.json` |
| 사전 검사 | `run_web_gpu.bat` kernel PASS | `CHECK_GPU_CUDA.bat` kernel PASS |
| 앱 선택 | `연산 장치 > NVIDIA GPU` | `연산 장치 > NVIDIA GPU` |
| run 증명 | Compute 행 GPU 이름 + GPU batch > 0 | Compute 행 GPU 이름 + GPU batch > 0 |
| 비교 | 동일 장면 warm 2·3회 | 동일 장면 warm 2·3회 |

이 네 단계 중 하나라도 빠지면 “GPU 가속 확인 완료”로 판정하지 않는다.
