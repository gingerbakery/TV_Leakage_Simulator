# PERF-3C GPU CUDA 데스크톱 패키징

## 목적

PERF-3C CUDA backend가 개발 환경뿐 아니라 전달용 데스크톱 ZIP에서도 실제
동작하게 하되, GPU가 없는 사용자의 기존 Lite/CPU 배포본 크기와 시작 경로는
바꾸지 않는다.

## 구현

- 기존 `build_lightweight_desktop.ps1`의 기본값은 `lite`로 유지했다.
- `gpu_cuda` opt-in edition과 전용 one-click wrapper를 추가했다.
  - `build_gpu_cuda_desktop.bat`
  - `build_gpu_cuda_desktop.ps1`
- GPU edition에만 다음 runtime을 복사한다.
  - `numba==0.66.0`
  - `llvmlite==0.48.0`
  - `llvmlite.libs`와 `llvmlite/binding/llvmlite.dll`
- Toolkit 자체는 패키지에 넣지 않는다. NVIDIA driver와 CUDA Toolkit은 대상
  PC에 설치되어 있어야 한다.
- `scripts/verify_gpu_cuda_runtime.py`가 import-only 진단과 실제 device kernel
  smoke를 분리해서 제공한다.
- `CHECK_GPU_CUDA.bat`을 GPU package 루트에 넣어 사용자가 더블클릭으로 같은
  device kernel 검증을 실행할 수 있게 했다.
- 빌드 중 staged package와 ZIP 재추출본 모두에서 실제 CUDA FP64 kernel을
  실행한다. 빌드 시 성공한 device 정보는 strict JSON manifest로 남긴다.
- 깊은 worktree에서 OCP의 Windows DLL 경로 제한을 피할 수 있도록 opt-in
  `-ReleaseDirectory` 출력 경로도 지원한다.
- ZIP 재추출 검증 폴더는 짧은 고유 token 이름을 사용해 표준 출력명이 긴
  경우에도 OCP의 legacy Windows DLL 경로 제한을 넘지 않게 했다.

## CPU 호환성

- `build_lightweight_desktop.bat`의 edition 기본값과 package 목록은 기존대로다.
- Lite package에는 Numba/llvmlite가 계속 포함되지 않는다.
- 앱의 기본 compute mode도 CPU이므로 CUDA module probe가 발생하지 않는다.
- GPU edition에서도 CUDA가 unavailable 또는 hard failure이면 logical batch를
  CPU에서 한 번 재실행하는 backend 계약을 사용한다.

## 검증 기준

- Python 3.13.3 x64
- NumPy 2.4.6
- Numba 0.66.0
- llvmlite 0.48.0
- CUDA Toolkit 13.1 (`windows_cuda13_x64_compat` layout)
- NVIDIA GeForce RTX 3070, compute capability 8.6

## 실제 package 결과

최종 source freeze를 표준 배포명으로 각각 전체 빌드했다.

| Edition | Folder | ZIP | Numba/llvmlite |
| --- | ---: | ---: | --- |
| Lite/CPU | 354.299MiB | 101.545MiB | 미포함 |
| GPU CUDA | 481.799MiB | 145.365MiB | 포함 |
| 증가분 | 127.500MiB | 43.820MiB | GPU edition에만 적용 |

GPU build는 staged package와 ZIP 재추출본 모두 RTX 3070에서 실제 kernel을
실행했고, STEP import·React build·통합 server를 통과했다. 최종 저장소
회귀 suite는 GPU runtime에서 `226개`, Lite runtime에서 `226개(23 skipped)`가
통과했다. Lite 내장 Python에서 Numba와 llvmlite가 모두 발견되지 않음도
별도로 확인했다.

## 최종 표준 산출물

- Lite ZIP: `release/leakage_simulator_desktop_v1.0.0_lite.zip`
  - `106,477,552 bytes` (`101.545MiB`)
  - SHA-256:
    `0b31b7df469172dbc79a3f7a7d20a9f9c2c604ebe2329b6344f6c5e03f7da5c7`
- GPU ZIP: `release/leakage_simulator_desktop_v1.0.0_gpu_cuda.zip`
  - `152,426,436 bytes` (`145.365MiB`)
  - SHA-256:
    `6bfbd10f7797a5d60cf098e815d4c09b1ad91ea4a32a0b1ef672fddf85ee3b3c`
- Device smoke: RTX 3070, compute capability 8.6, CUDA 13 Windows compatibility
  layout, 실제 FP64 JIT kernel 성공
- 두 ZIP 내부 source SHA:
  - `raytracer.py`:
    `daa3128dec93c5f82342e582919c54c4f65575f3a27512bd135e5f3331e85092`
  - `geometry.py`:
    `ac7339508a0868ed6ee2ee782dd5ca62783cb2ad44fb192b54cd4cdcd49f96c6`
