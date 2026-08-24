# 데스크톱 EXE 패키징 가이드

## 목적
- 브라우저 명령 입력 없이 더블클릭만으로 시뮬레이터를 실행할 수 있게 한다.
- React production UI와 FastAPI 계산 서버를 한 패키지로 제공한다.
- 사내 시연 및 테스트 배포를 쉽게 만든다.

## 현재 방식
- `LeakageSimulator.exe`는 얇은 데스크톱 런처다.
- 런처는 내부적으로:
  - embedded Python 실행
  - `run_web.py`를 통해 FastAPI·Uvicorn 서버 실행
  - local `127.0.0.1` 포트 대기
  - 동일 서버의 React production UI를 WebView2로 표시

## 경량 STEP/STP 배포본
- 빌드 명령: `.\build_lightweight_desktop.bat`
- 출력 폴더: `release/leakage_simulator_desktop_v1.0.0_lite/`
- 전달 파일: `release/leakage_simulator_desktop_v1.0.0_lite.zip`
- 사용자는 ZIP을 정상적으로 압축 해제한 뒤 `LeakageSimulator.exe`를 더블클릭한다.
- 내장 WebView2 초기화가 실패하면 기본 브라우저로 local UI를 연다.

## PERF-3C NVIDIA CUDA 배포본

- 빌드 명령: `.\build_gpu_cuda_desktop.bat`
- 출력 폴더: `release/leakage_simulator_desktop_v1.0.0_gpu_cuda/`
- 전달 파일: `release/leakage_simulator_desktop_v1.0.0_gpu_cuda.zip`
- 사용자 실행 가이드: 배포본의 `docs/gpu-cuda-user-guide.md`
  - 저장소 기준 문서: `docs/gpu-cuda-user-guide.md`
- AI 실행 진입점: 배포본의 `AGENTS.md`
  - 공통 런북: `docs/ai-gpu-execution-runbook.md`
  - Claude/Gemini/Copilot용 얇은 진입 파일도 같은 런북으로 연결한다.
- 기본 Lite 배포본과 분리된 opt-in 에디션이다. 실제 동등 빌드 비교에서
  GPU 의존성 증가분은 폴더 `127.5MB`, ZIP `43.8MB`였으며 Lite/CPU 사용자는
  이 파일을 추가로 받지 않는다.
- GPU 에디션에는 `numba==0.66.0`, `llvmlite==0.48.0` 및 네이티브
  `llvmlite.dll`이 포함된다.
- CUDA Toolkit과 NVIDIA 드라이버는 재배포하지 않고 대상 PC의 설치본을
  사용한다.

### Source 실행과 ZIP 실행은 별도 경로

| 대상 | 전달 | 진입점 | 동기화/검증 |
| --- | --- | --- | --- |
| 개발자·source 사용자 | Git branch/commit | `run_web_gpu.bat` | `.venv-gpu`, 두 requirements, `npm ci`, frontend build, production Ray/BVH CUDA kernel |
| 일반 GPU 테스터 | GPU ZIP + sidecar + handoff manifest | `CHECK_GPU_CUDA.bat` → `LeakageSimulator.exe` | 내장 runtime + 현재 PC production Ray/BVH CUDA kernel |

- `git pull`은 무시되는 `_tools`, `.venv-gpu`, `frontend/dist`, 기존 EXE와
  이미 압축 해제한 ZIP을 직접 업데이트하지 않는다.
- Source launcher는 이를 보완하기 위해 requirements fingerprint가 바뀌면
  `.venv-gpu`를 재생성하고, 매 실행마다 Python resolver/exact-pin 검사 및
  `npm ci`/production build를 수행한다.
- GPU preflight가 실패하면 `run_web_gpu.bat`은 서버를 시작하지 않는다.
  CPU fallback이 목적이라면 별도 `run_web.bat`을 사용한다.
- 서버를 띄우지 않고 source 환경만 점검하려면
  `.\run_web_gpu.ps1 -PreflightOnly`을 실행한다.
- 일반 테스터에게 branch pull과 EXE 실행을 섞어서 안내하지 않는다.

Windows의 긴 경로 아래 worktree에서 빌드한다면 `-ReleaseDirectory`로 짧은
release 경로를 지정할 수 있다. 일반 저장소 루트의 one-click 빌드에는 필요
없다.

### 대상 PC 요구 사항

- 64-bit Windows와 NVIDIA CUDA 지원 GPU
- 설치된 GPU에 맞는 NVIDIA display driver
- CUDA Toolkit. 현재 검증 기준은 CUDA Toolkit `13.1`이며 provider가
  Windows CUDA 13의 `bin/x64`, `nvvm/bin/x64`, `nvvm/libdevice` 구조를
  명시적으로 탐색한다.
- Toolkit에는 `cudart64_*.dll`, `nvvm*.dll`, `libdevice*.bc`가 모두 있어야
  한다. `CUDA_PATH` 또는 표준 NVIDIA 설치 경로로 찾을 수 있어야 한다.

GPU를 선택했지만 드라이버·Toolkit·GPU가 없거나 실행 중 CUDA 오류가 나면
해당 logical batch 전체를 CPU에서 한 번 재실행한다. 기본 compute mode는
계속 CPU이며, Lite 배포본은 CUDA probe나 Numba import를 하지 않는다.

### GPU 패키지 검증

빌드 스크립트는 패키징 전과 ZIP 재추출 후에 각각 다음을 확인한다.

1. Numba/llvmlite 버전 pin과 `llvmlite.dll` 실제 로드
2. PERF-3C provider의 CUDA driver/Toolkit/device probe
3. 실제 production BVH scene upload와 hit/miss Ray CUDA 결과의 FP64 일치
4. strict JSON 결과를 `gpu_cuda_runtime_manifest.json`에 기록

의존성 import만 확인해야 하는 CPU-only 진단에서는 다음 명령을 쓸 수 있다.

```powershell
_tools\python313\python.exe scripts\verify_gpu_cuda_runtime.py --mode imports
```

실제 GPU와 Toolkit까지 확인하려면 `--mode device`를 사용한다.
배포받은 사용자는 패키지 루트의 `CHECK_GPU_CUDA.bat`을 더블클릭하면 같은
device 검증을 내장 Python으로 실행할 수 있다. GPU 이름, 실제 Ray/BVH kernel PASS,
마지막 `[OK]` 확인 후 GPU mode를 선택한다. 오류 시 checker는 driver,
Toolkit, Python runtime별 `[ACTION]`을 표시한다.

### 테스터용 GPU ZIP handoff

Commit과 binary를 혼동하지 않도록 clean worktree에서 다음 helper를 실행한다.

```powershell
.\prepare_gpu_cuda_test_release.bat
```

helper는 기존 GPU packaging 검증을 수행한 뒤 다음 세 파일이 서로 일치하는지
확인한다.

```text
release/leakage_simulator_desktop_v1.0.0_gpu_cuda.zip
release/leakage_simulator_desktop_v1.0.0_gpu_cuda.zip.sha256
release/leakage_simulator_desktop_v1.0.0_gpu_cuda.zip.handoff.json
```

handoff manifest에는 branch, 40자리 commit, ZIP byte size·SHA-256, source용
entrypoint, packaged tester용 entrypoint, AI instruction entrypoint와 GPU
runbook 경로가 기록된다. 세 파일을 함께 전달하고 테스터는 새 폴더에 압축
해제한다. GitHub Release 업로드는 별도 승인된 배포 작업이며 이 helper가 외부
게시를 수행하지는 않는다.

테스터 결과를 받을 때는 다음 증거를 같이 요청한다.

1. `.handoff.json`의 commit과 SHA-256
2. `CHECK_GPU_CUDA.bat`의 GPU 이름과 Ray/BVH kernel PASS
3. 결과 창 전체 `Compute` 행과 GPU/fallback batch count
4. 같은 장면 warm 2·3회 시간, emitter 종류, `.bitsam` 식별 정보

`BVH build ... Rebuilt`만으로 GPU 사용 여부를 판정하지 않는다. BVH는 CUDA도
사용하는 acceleration structure이며 실제 장치 사용은 `Compute` evidence로
판정한다.

현재 표준 GPU 산출물 실측은 폴더 `481.8MB`, ZIP `145.4MB`다. 빌드마다
frontend asset 이름과 문서가 바뀔 수 있어 소수점 단위 크기는 달라질 수 있다.

### PERF-3D release 동기화

PERF-3D는 GPU intersection package 자체를 바꾸는 단계가 아니라 GPU run의 host
overhead를 줄이는 source 변경이다. 따라서 Lite와 GPU ZIP을 같은 PERF-3D source와
문서로 모두 다시 빌드한다.

- Lite는 CPU `auto -> per_tape`, CUDA/Numba no-import/no-probe를 보존한다.
- GPU edition은 `auto -> run_accumulator`, vector seed/numeric Receiver와
  stored-path payload suppression을 포함한다.
- 기존 PERF-3C ZIP을 이름만 바꿔 PERF-3D로 배포하지 않는다.
- 두 ZIP 모두 재추출한 패키지 안의 `raytracer.py`, ordered reducer와 기존 포함
  문서인 README, AGENTS, AI GPU runbook, GPU user guide, backend contract,
  performance plan, desktop packaging guide가 build source와 같은지 stream
  hash로 확인한다. 상세 PERF-3D change report는 repository-only이며 package
  복사 범위를 늘리지 않는다.
- GPU ZIP은 재추출 뒤 production Ray/BVH device/kernel 검증, Lite ZIP은 CPU/no-probe smoke를
  실행한다.
- ZIP SHA-256은 최종 문서 동기화 뒤 다시 만든 산출물을 기준으로 한다. ZIP 내부
  문서에는 자기 ZIP의 hash를 넣지 않아 문서-hash 자기참조를 피한다.
- 전달 시 최종 hash와 byte size는 ZIP 밖의 `<zip-name>.sha256` sidecar와 release
  보고에서 확인한다.

### 포함 기능
- STEP/STP 실제 import와 OCP tessellation
- React + TypeScript App Shell
- Three.js CAD Viewer
- ROI와 Component/Transform/Material UI
- Emitter/Receiver 배치
- Result 분석 창과 ray path 시각화
- RT-2A CAD 차폐
- RT-2B optical property 조회
- RT-2C Specular/Gaussian/Lambertian 1회 반사
- PERF-1 Python hot path 최적화
- PERF-2 flat BVH CAD 교차 가속
- PERF-3C strict-float64 CUDA/hybrid stack(GPU edition)
- PERF-3D host-overhead 제거와 run-retained ordered accumulator

### 최소 런타임
- Python 3.13 embedded runtime
- FastAPI·Uvicorn·Pydantic
- `OCP`
- OCP가 직접 연결하는 CAD/VTK DLL dependency closure
- `NumPy`
- 전체 CadQuery, VTK Python module, SciPy, PyArrow, Jupyter 등은 제외
- GPU 에디션에만 Numba·llvmlite를 추가하며 CUDA Toolkit은 포함하지 않음

### 제외 기능
- X_T 직접 import는 아직 구현되지 않았다.
- matplotlib 기반 legacy PNG export는 경량판에서 생략될 수 있다.
- Web UI의 Receiver heatmap과 ray path 시각화는 계속 사용할 수 있다.

### 검증
- 빌드 시 실제 샘플 STP가 synthetic fallback 없이 import되는지 확인한다.
- 현재 Python unit test 84개를 최소 런타임으로 실행한다.
- ZIP을 다시 열어 필수 파일을 확인한다.
- ZIP을 별도 검증 폴더에 풀고 OCP/STP import와 React 통합 서버를 다시
  실행한다.
- ZIP SHA-256 파일을 함께 생성한다.
- EXE 실행기는 서버 시작을 최대 180초 기다리며 `desktop_runtime/launcher.log`에 단계별 진단을 기록한다.
- 웹 서버는 먼저 기동하고 무거운 OCP CAD 런타임은 STEP/STP import 시점에 지연 로드한다.

## 장점
- 별도 브라우저를 열 필요가 없다.
- STEP/STP import 흐름을 Python/OCP 쪽 그대로 활용할 수 있다.
- X_T 직접 import는 향후 별도 importer가 필요하다.
- 코딩 경험이 거의 없는 사용자도 더 쉽게 테스트 가능하다.

## 패키지 구성
- `LeakageSimulator.exe`
- `run_web.py`
- `run_api.py`
- `frontend/dist/`
- `src/`
- `_tools/python313/`
- `Microsoft.Web.WebView2.Core.dll`, `Microsoft.Web.WebView2.WinForms.dll`, `WebView2Loader.dll`
- WebView2 관련 DLL
- 필요 시 `samples/`, `_uploads/`, `outputs/`

## 제약 사항
- 경량판도 CAD/OCP 네이티브 DLL 때문에 압축 해제 후 수백 MB가 필요하다.
- target PC에 WebView2 runtime이 필요할 수 있다.
- `run_web.py`는 통합 서버 호환 진입점이며, 이전 인라인 UI는
  `run_web_legacy.py`에만 남고 패키지에는 포함되지 않는다.

## 빌드
- 사용 스크립트: `build_desktop_webview_exe.bat`
- 출력 폴더: `release/leakage_simulator_desktop_v1.0.0`
- 권장 경량 스크립트: `build_lightweight_desktop.bat`

## 운영 권장
- 개발 공유는 Git 저장소로 진행
- 실행 테스트/시연은 `release/` 패키지로 배포
