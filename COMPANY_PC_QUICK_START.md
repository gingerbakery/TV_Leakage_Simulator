# Company PC Quick Start

## 목적
- 회사 PC에서 React 데스크톱 패키지를 빠르게 실행하기 위한 안내

## 실행 방법
1. 압축 파일을 회사 PC의 짧은 경로에 풉니다.
   - 예: `C:\TV_leakage_simulator`
2. `LeakageSimulator.exe`를 더블클릭합니다.
3. `Leakage simulator ready`가 표시될 때까지 기다립니다.
4. Model Import에서 STEP/STP 파일을 선택합니다.
5. WebView2를 사용할 수 없는 환경에서는 기본 브라우저로 자동 전환됩니다.

## GPU 사용 전

- 파일 이름이 `*_gpu_cuda.zip`인 GPU 배포본만 NVIDIA GPU 모드를 제공한다.
- GPU 배포본도 NVIDIA driver와 CUDA Toolkit 13.1은 PC에 별도 설치해야 한다.
- 설치가 확실하지 않으면 `docs\WINDOWS_GPU_SETUP.md`를 읽고
  `setup_windows_gpu.bat`의 기본 점검 모드부터 실행한다.
- 설치 승인을 한 경우에만 `setup_windows_gpu.bat -Install`을 사용한다.
- 설치 후 `CHECK_GPU_CUDA.bat`이 실제 RTX A4000 production kernel PASS를
  표시해야 `LeakageSimulator.exe`에서 GPU를 선택한다.
- `*_lite.zip`은 CPU 전용이다. GPU가 설치되어 있어도 Lite EXE는 CUDA를 쓰지
  않는다.

## 포함 파일
- `LeakageSimulator.exe`
- `run_web.py`
- `run_api.py`
- `frontend\dist\...`
- `check_cad_import.py`
- `src\leakage_simulator\...`
- `_tools\python313\...`
- `samples\tv_leakage_full_assembled_no_gap.stp`

## 주의
- 이 패키지는 React production UI와 FastAPI를 함께 포함합니다.
- 개발용 Node.js나 npm은 필요하지 않습니다.
- 실제 STEP/STP import를 위해 embedded Python 런타임과 CAD 관련 라이브러리를 함께 포함합니다.
- 시작 로그는 `desktop_runtime\launcher.log`에서 확인할 수 있습니다.

## 권장 경로
- 가능하면 공백/한글/특수문자가 너무 많은 경로는 피합니다.
- 예:
  - `C:\TV_leakage_simulator`
