# NVIDIA CUDA GPU 가속 사용 가이드

이 문서는 `leakage_simulator_desktop_v1.0.0_gpu_cuda.zip` 사용자를 위한
체크리스트다. Lite ZIP에서는 GPU 가속을 사용할 수 없다.

## 1. 준비 확인

- [ ] 64-bit Windows PC다.
- [ ] CUDA를 지원하는 NVIDIA GPU가 있다.
- [ ] CUDA Toolkit `13.1`과 호환되는 NVIDIA display driver를 설치했다.
- [ ] CUDA Toolkit `13.1`을 설치했다.
- [ ] GPU CUDA ZIP을 받았다. Lite ZIP이 아니다.

GPU ZIP에는 Python, Numba `0.66.0`, llvmlite `0.48.0`이 들어 있다.
Python, Node.js, npm은 따로 설치하지 않는다.

다음 항목은 패키지에 포함되지 않으므로 PC에 직접 설치해야 한다.

- NVIDIA display driver
- CUDA Toolkit `13.1`

설치 후 Windows를 다시 시작하는 것을 권장한다.

## 2. 압축 해제

1. ZIP 파일을 우클릭하고 `압축 풀기`를 선택한다.
2. 짧은 로컬 경로에 폴더 전체를 푼다.
   - 권장: `C:\TV_leakage_simulator_gpu`
3. ZIP 내부에서 EXE를 바로 실행하지 않는다.
4. `_tools`, `src`, `frontend`, `scripts` 폴더를 이동하거나 삭제하지 않는다.

이 프로그램은 설치 마법사가 없는 portable 패키지다. 기존 사용자는 새 GPU
ZIP을 새 폴더에 풀어야 이번 GPU 최적화를 사용할 수 있다.

## 3. GPU 사전 검사

1. 압축을 푼 폴더에서 `CHECK_GPU_CUDA.bat`을 더블클릭한다.
2. 검사 창이 끝날 때까지 기다린다.
3. 마지막의 다음 문구를 확인한다.

```text
[OK] NVIDIA CUDA runtime and a real GPU kernel are working.
```

이 검사는 GPU 이름 확인만 하지 않는다. 내장 Python으로 실제 FP64 CUDA
kernel을 실행하고 결과까지 검증한다.

`[FAIL]`이면 우선 CPU 모드를 사용하고 [문제 해결](#7-문제-해결)을 확인한다.

## 4. 앱에서 GPU 선택

1. `LeakageSimulator.exe`를 더블클릭한다.
2. CAD와 `.bitsam` 프로젝트를 평소처럼 연다.
3. `Ray Tracing` 패널의 `Run Options`를 펼친다.
4. `Compute backend`에서 `NVIDIA CUDA GPU`를 선택한다.
5. `Run Ray Tracing`을 누른다.

`Compute backend` 옆 도움말 아이콘을 누르면 요구 사항과 fallback 설명을 앱
안에서 다시 볼 수 있다.

## 5. 실제 GPU 사용 확인

계산이 끝나면 결과 창의 `Compute` 항목을 확인한다.

| 표시 | 의미 |
| --- | --- |
| `GPU_CUDA · gpu_cuda · <GPU 이름>` | GPU provider가 사용됨 |
| `GPU_CUDA · mixed · <GPU 이름>` | GPU와 작은 wave용 CPU provider가 함께 사용됨 |
| `CPU small-wave batches ...` | 작은 batch를 CPU로 처리함. 정상 hybrid 동작 |
| `CPU fallback (...)` | CUDA 실행 실패 후 해당 작업 단위를 CPU로 재계산함 |
| `CPU (...)` | GPU를 사용할 수 없어 CPU 경로를 사용함 |

GPU 이름이 보이고 fallback 사유가 없다면 GPU 가속이 정상 동작한 것이다.

## 6. 프로젝트 기본값

- 새 프로젝트의 기본 backend는 `CPU`다.
- `compute_backend` 항목이 없는 기존 `.bitsam`도 안전하게 `CPU`로 열린다.
- 현재 버전에서 GPU를 선택해 저장한 `.bitsam`은 그 선택을 복원할 수 있다.
- 다른 PC에서 프로젝트를 열면 `CHECK_GPU_CUDA.bat`을 다시 실행한다.
- GPU를 쓰려면 프로젝트마다 `NVIDIA CUDA GPU` 선택을 확인한다.

## 7. 문제 해결

### `cuda_driver_unavailable`

- NVIDIA display driver 설치 여부를 확인한다.
- 설치 후 Windows를 다시 시작한다.
- 원격 데스크톱이나 가상 환경이라면 GPU가 현재 세션에 노출되는지 확인한다.

### `cuda_toolkit_not_found`

- CUDA Toolkit `13.1`이 설치됐는지 확인한다.
- 기본 설치 경로를 권장한다.
- 사용자 지정 경로라면 `CUDA_PATH`가 Toolkit 루트를 가리키는지 확인한다.
- Toolkit 폴더에 다음 파일이 있는지 확인한다.
  - `bin\x64\cudart64_*.dll`
  - `nvvm\bin\x64\nvvm*.dll`
  - `nvvm\libdevice\libdevice*.bc`

### `numba_not_installed` 또는 Numba import 오류

- Lite ZIP을 실행한 것은 아닌지 확인한다.
- GPU ZIP을 다시 다운로드하고 새 폴더에 전체 압축 해제한다.
- `_tools` 폴더를 다른 패키지의 파일로 덮어쓰지 않는다.

### 앱은 실행되지만 결과가 CPU로 표시됨

- `Run Options > Compute backend`가 `NVIDIA CUDA GPU`인지 확인한다.
- 결과 창의 `CPU fallback (...)` 또는 `CPU (...)` 사유를 확인한다.
- 앱을 닫고 `CHECK_GPU_CUDA.bat`을 다시 실행한다.

### 앱 자체가 열리지 않음

- `desktop_runtime\launcher.log`를 확인한다.
- 패키지를 더 짧은 로컬 경로에 다시 푼다.
- 폴더 일부가 누락되지 않았는지 확인한다.

`gpu_cuda_runtime_manifest.json`은 패키지를 만든 PC의 검증 기록이다. 현재 PC의
동작 확인에는 반드시 `CHECK_GPU_CUDA.bat` 결과를 사용한다.

## 8. 성능 기대와 제약

- 실제 RTX 3070, ROI 100만 primary ray, 반사 깊이 10 기준 측정은
  `7.28초 → 5.54초`였다.
- 같은 조건에서 지연시간은 약 `23.9%` 감소했다.
- 이는 이전 PERF-3C GPU build와 현재 build의 비교이며 CPU 대비 향상률이 아니다.
- 장면, ray 수, 반사 깊이, GPU에 따라 향상 폭은 달라진다.
- 모든 장면에서 GPU가 CPU보다 빠르다고 보장하지 않는다.
- 작은 계산은 초기 JIT compile과 전송 비용 때문에 CPU보다 빠르지 않을 수 있다.
- 첫 GPU 실행보다 같은 앱 세션의 후속 실행이 더 빠를 수 있다.
- GPU 가속 대상은 ray tracing 계산이다. CAD import와 UI 전체가 빨라지는 것은
  아니다.
- 현재 구현은 strict FP64 CUDA/hybrid 경로다. 전체 계산을 하나의 GPU kernel에
  상주시킨 완전 fused 구현은 아니다.
- GPU 오류 시 결과를 버리지 않고 해당 logical batch를 CPU로 한 번 재계산한다.

## 빠른 확인표

| 단계 | 완료 기준 |
| --- | --- |
| 패키지 | GPU CUDA ZIP을 새 폴더에 전체 압축 해제 |
| PC 준비 | NVIDIA driver + CUDA Toolkit `13.1` 설치 |
| 검사 | `CHECK_GPU_CUDA.bat`에서 `[OK]` |
| 선택 | `Run Options > Compute backend > NVIDIA CUDA GPU` |
| 확인 | 결과 창 `Compute`에 GPU 이름 표시 |
