# Windows GPU prerequisite setup and AI handoff

## 목적

RTX A4000을 사용하는 회사 Windows PC에서도 사람과 저장소-aware AI가 같은
설치·검증 절차를 따르게 한다. Package 설치만으로 GPU 성공을 주장하거나,
누락된 명령을 발견한 AI가 승인 없이 driver·Toolkit·재부팅을 수행하는 일을
막는다.

## 변경

- `docs/WINDOWS_GPU_SETUP.md`
  - Source/GPU ZIP/Lite delivery path를 먼저 구분한다.
  - RTX A4000 Compute Capability 8.6, CUDA 13.x driver 580 이상, CUDA
    Toolkit 13.1 Update 1의 공식 요구사항을 기록한다.
  - Python 3.13.15 x64와 Node.js 24.19.0 LTS x64는 source에만 필요한 것으로
    분리한다.
  - 읽기 전용 inventory, 설치 승인, 재부팅 별도 승인, production Ray/BVH
    preflight, 실제 run의 CUDA batch 증명을 단계별로 정의한다.
  - 사내 IT 요청문과 AI용 복사·붙여넣기 prompt를 제공한다.
- `setup_windows_gpu.bat` / `setup_windows_gpu.ps1`
  - 기본 실행은 시스템을 바꾸지 않는 점검 전용이다.
  - 명시적인 `-Install`에서만 고정 WinGet package를 설치한다.
  - driver가 없거나 최소 버전에 못 미치면 임의 다운로드·CPU 대체 없이
    fail-closed한다.
  - 자동 재부팅, security hash 무시, 기존 설치 강제 제거를 하지 않는다.
- AI entrypoint와 사용자 문서
  - `AGENTS.md`, Claude/Gemini/Copilot 안내, README, 공통 AI runbook, GPU
    사용자 가이드가 Windows setup 문서를 필수 진입점으로 연결한다.
- Source/GPU launcher와 package handoff
  - source launcher 오류가 setup 문서 경로를 보여준다.
  - Lite/GPU package에 문서를 복사하고 GPU ZIP handoff manifest가 존재를
    검증한다.

## 안전 경계

- 이번 변경은 현재 개발 PC에 driver, CUDA Toolkit, Python 또는 Node를
  설치하거나 제거하지 않았다.
- 설치 모드는 사용자가 명시적으로 실행해야 한다.
- UAC, 관리자 자격 증명, 회사 Software Center/AppLocker/네트워크 정책은
  사용자 또는 사내 IT의 권한 범위다.
- 재부팅은 helper가 수행하지 않으며 필요할 때 사용자의 별도 승인을 받는다.
- GPU 준비 완료는 package 설치가 아니라 `production_ray_bvh` kernel 검증과
  실제 run의 `gpu_cuda_gpu_success_count > 0`으로 판정한다.

## 검증

- AI guidance, source bootstrap, GPU packaging 회귀 테스트
- Windows setup helper의 check-only/install-gate 정적 회귀 테스트
- PowerShell AST parse
- 전체 Python test suite
- `git diff --check`
