# TV Leakage Simulator

TV 기구 개발 단계에서 발생 가능한 `빛샘(light leakage)`을 빠르게 예측하기 위한 전용 시뮬레이터입니다.  
목표는 기구 설계자도 광학 전용 상용 툴 없이 `ROI 선택 → gap/transform 설정 → 광원 배치 → 상대/대략 절대 밝기 비교`까지 수행할 수 있게 만드는 것입니다.

## 현재 범위
- V1 기준 경량 시뮬레이터
- 분광/시감도(M2/M3)는 보류
- CAD import, ROI 선택, gap/transform, 간략 ray tracing, 결과 시각화 중심

## 현재 제공 모드

### 1. React 개발 UI + FastAPI
- 주요 파일:
  - `frontend/`
  - `run_api.py`
  - `src/leakage_simulator/api/`
- 목적:
  - CAD import
  - ROI 선택
  - Component / Material / Transform
  - Emitter / Receiver / Ray tracing
  - Result 및 광선 경로 시각화

실행 예시(터미널 2개):

```powershell
python run_api.py --port 8788 --strict-port
```

```powershell
cd frontend
npm install
npm run dev
```

브라우저 주소: `http://127.0.0.1:5173/`

### 2. React production 통합 서버
- `frontend` production build와 FastAPI를 한 포트에서 실행한다.

가장 간단한 실행 방법은 프로젝트 루트의 `run_web.bat`을
더블클릭하는 것입니다. 새 clone 환경에서는 `.venv`와 frontend package를
최초 1회 자동으로 준비하고 `http://127.0.0.1:8788/`을 엽니다.

GPU를 검증할 source 사용자는 `run_web.bat` 대신 `run_web_gpu.bat`을
더블클릭합니다. 이 파일은 전용 `.venv-gpu`, CPU/GPU requirements와 frontend를
pull된 source에 맞게 동기화하고 실제 production Ray/BVH CUDA kernel이 통과한 경우에만 서버를
시작합니다. `[GPU VERIFIED]`와 ray tracing 결과의 `Compute` 행을 모두
확인해야 실제 GPU 사용으로 판정합니다.

```powershell
npm --prefix frontend install
npm --prefix frontend run build
python run_web.py --port 8788 --strict-port
```

브라우저 주소: `http://127.0.0.1:8788/`

기존 인라인 UI 소스는 `run_web_legacy.py`에 참조용으로만 보존한다.

### 3. CLI 실행
- 주요 파일: `run.py`
- 목적:
  - 시뮬레이션 코어 검증
  - 기본 출력(JSON/CSV/PNG) 확인

예시:

```powershell
python run.py --rays 4000 --max-depth 2 --seed 42 --output outputs
```

### 4. 데스크톱 EXE 패키지
- 목적:
  - 더블클릭 기반 내부 시연
  - 웹 UI를 별도 브라우저 없이 내장 WebView 창에서 실행
- 관련 문서: `docs/desktop-exe-packaging.md`

경량 STEP/STP 배포본 생성:

```powershell
.\build_lightweight_desktop.bat
```

NVIDIA CUDA GPU 가속이 필요한 PC용 별도 배포본 생성:

```powershell
.\build_gpu_cuda_desktop.bat
```

다른 사용자에게 검증 가능한 GPU ZIP을 전달할 때는 다음 helper를 사용합니다.

```powershell
.\prepare_gpu_cuda_test_release.bat
```

helper는 clean commit에서 ZIP을 다시 만들고 commit·byte size·SHA-256이 담긴
`.handoff.json`을 생성합니다. ZIP, `.sha256`, `.handoff.json`을 함께
전달합니다. `git pull`은 이미 압축 해제한 EXE나 ZIP을 갱신하지 않습니다.

GPU 배포본은 Numba/llvmlite를 추가로 포함하지만 CUDA Toolkit과 NVIDIA
드라이버는 대상 PC에 별도 설치되어 있어야 한다. 기본 경량 배포본에는 이
의존성을 넣지 않으므로 CPU-only PC의 크기와 실행 경로는 기존과 같다.

- GPU 사용자 설치·검사·선택 가이드:
  [`docs/gpu-cuda-user-guide.md`](docs/gpu-cuda-user-guide.md)
- Lite/GPU 배포 ZIP 안에서도 `docs/gpu-cuda-user-guide.md`로 제공한다.

### AI를 통한 GPU 실행

Codex는 저장소 또는 압축 해제한 패키지 루트를 파일 접근 권한과 함께 열면
루트의 `AGENTS.md`를 자동 발견해 GPU 실행 규칙의 첫 진입점으로 사용한다.
Claude, Gemini, GitHub Copilot용 얇은 안내 파일도 같은 공통 런북으로
연결한다. 각 도구의 버전·설정에 따른 자동 발견 차이가 있으므로 실제로 문서를
읽었는지는 확인해야 한다. Lite/GPU ZIP에도 이 파일들을 함께 넣는다.

다만 모든 웹 채팅 AI가 로컬 폴더를 자동으로 읽을 수 있는 공통 표준은 없다.
AI에 저장소/패키지 파일 접근 권한이 없거나 루트가 아닌 곳에서 시작했다면
자동 안내는 보장되지 않는다. 이때는 아래 프롬프트를 전달하고, 필요하면 세
문서를 첨부한다.

```text
이 프로젝트의 GPU 실행을 맡아줘. 명령을 실행하기 전에 저장소/압축 해제 폴더
루트의 AGENTS.md, docs/ai-gpu-execution-runbook.md,
docs/gpu-cuda-user-guide.md를 끝까지 읽고 그대로 따라줘. 먼저 Source/GPU ZIP/
Lite ZIP 중 전달 경로를 식별하고, production_ray_bvh 사전 검사와 완료된 실행의
Compute 상태 및 CUDA 성공 batch 수를 모두 확인하기 전에는 GPU 성공이라고
말하지 마. 드라이버·CUDA Toolkit 설치나 재부팅은 내 명시적 승인을 먼저 받아.
```

- AI 공통 GPU 실행 런북:
  [`docs/ai-gpu-execution-runbook.md`](docs/ai-gpu-execution-runbook.md)
- 저장소 전체 AI 규칙: [`AGENTS.md`](AGENTS.md)

- 출력: `release/leakage_simulator_desktop_v1.0.0_lite/`
- 전달용 ZIP: `release/leakage_simulator_desktop_v1.0.0_lite.zip`
- 사용자는 압축 해제 후 `LeakageSimulator.exe`만 더블클릭
- React UI, FastAPI, STEP/STP import, ROI, Material, Transform,
  Emitter·Receiver, 광선 결과 시각화 포함
- X_T 직접 import와 legacy matplotlib PNG export는 경량판 범위에서 제외

## 저장소 구조
- `src/leakage_simulator/`
  - 코어 엔진, CAD import, ROI, gap, ray tracing, 렌더링, FastAPI 계층
- `frontend/`
  - React + TypeScript UI
- `run_api.py`
  - React용 FastAPI 서버 진입점
- `run_web.py`
  - React production + FastAPI 통합 서버의 호환 엔트리
- `run_web_legacy.py`
  - 배포에서 제외되는 이전 인라인 UI 참조 소스
- `desktop_launcher/`
  - 내장 WebView 데스크톱 런처 소스
- `docs/`
  - 요구사항, 설계, 협업 규칙, material 구조, 시작 가이드
- `samples/`
  - 소형 샘플 자산

## 주요 문서
- AI 저장소 지침: `AGENTS.md`
- AI GPU 실행 런북: `docs/ai-gpu-execution-runbook.md`
- 요구사항: `docs/requirements.md`
- 아키텍처: `docs/design.md`
- ROI/Gap/Ray trace 계약: `docs/backend-data-contracts.md`
- 개발 역할 경계: `docs/developer-ownership.md`
- 개발자 시작 가이드: `docs/developer-start-guide.md`
- Git 협업 가이드: `docs/git-collaboration-guide.md`
- Material 구조: `docs/material-library.md`
- Material UI 구조: `docs/material-library-ui.md`
- Web UI 흐름: `docs/web-ui.md`

## 실행 관련 주의사항
- Git 저장소에는 `_tools/` 런타임이 기본적으로 포함되지 않도록 설정되어 있습니다.
- CPU source는 `run_web.bat`, GPU source는 `run_web_gpu.bat`으로 환경을
  자동 준비합니다. Python 3.13이 설치되지 않은 PC에서는 먼저 설치가 필요합니다.
- 실행이 바로 필요하면 아래 중 하나가 필요합니다:
  - Source: 시스템 Python 3.13 + Node.js 후 해당 one-click launcher
  - 일반 테스터: 전체 runtime이 포함된 `release/` 패키지 전달

## 협업 권장 방식
- `main`: 통합 안정 브랜치
- 기능별 브랜치:
  - `feature/cad-import-roi`
  - `feature/transform-gap`
  - `feature/material-library`
  - `feature/desktop-packaging`

## 현재 문서 운영 원칙
- 구현 변경은 `docs/changes/*.md`에 날짜별 기록
- 설계 결정은 `docs/*.md` 기준 문서로 관리
- 코드 변경과 문서 변경을 가능한 한 같이 유지

## 한 줄 요약
- 이 프로젝트는 `CAD 프로그램 스타일 UX + 빛샘 특화 시뮬레이션`을 목표로 하는 TV 기구 설계용 전용 툴입니다.
