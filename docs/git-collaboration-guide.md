# Git 협업 가이드

## 목적
- 저장소를 소스코드와 문서 중심으로 관리한다.
- 무거운 런타임/출력물을 Git history에서 제외한다.
- 여러 개발자가 기능별로 나누어 작업하기 쉽게 만든다.

## Git에 포함할 것
- `src/`
- `docs/`
- `samples/`(작고 공유 가능한 파일만)
- `run.py`
- `run_web.py`
- `run_gui.py`
- `desktop_launcher/`
- 실행/빌드 스크립트
- `README.md`

## Git에 포함하지 않을 것
- `_tools/`
- `release/`
- `outputs/`
- `_uploads/`
- `__pycache__/`
- `.matplotlib/`
- 로컬 IDE 설정

## 왜 `.gitignore`가 중요한가
- 큰 런타임이 한 번 들어가면 저장소가 계속 무거워진다.
- 출력 파일과 업로드 CAD는 협업용 코드 관리 대상이 아니다.
- 테스트 배포 산출물은 별도 전달이 더 적합하다.

## 권장 첫 업로드 순서

### 1단계: 문서/규칙
- `.gitignore`
- `README.md`
- `docs/`
- `COMPANY_PC_QUICK_START.md`

### 2단계: 코어 백엔드
- `src/`
- `run.py`
- `check_cad_import.py`
- `check_cad_import.bat`

### 3단계: Web UI
- `run_web.py`
- `run_web.bat`
- `run_web_dev.py`
- `run_local.bat`
- `start_web_dev.bat`
- `start_web_v3.bat`
- `stop_web_ui.bat`

### 4단계: 데스크톱 패키징
- `desktop_launcher/`
- `build_desktop_webview_exe.bat`
- `run_gui.py`
- `build_gui_exe.bat`

## 왜 커밋을 나눠야 하나
- 리뷰가 쉬워진다.
- 문제가 생겼을 때 원인 커밋을 찾기 쉽다.
- 기능별 담당자가 자기 영역만 보기 좋다.

## 브랜치 전략 권장안
- `main`
  - 안정 통합 브랜치
- `feature/cad-import-roi`
- `feature/transform-gap`
- `feature/material-library`
- `feature/desktop-packaging`

## 기본 명령 개념
- `git add`
  - 이번 커밋에 넣을 파일 선택
- `git commit`
  - 로컬 Git 기록에 저장
- `git push`
  - 원격 저장소에 업로드

## 협업 시 권장 흐름
1. `main` 최신화
2. 기능 브랜치 생성
3. 기능 구현
4. 문서/코드 같이 커밋
5. push 후 공유

## `_tools/`를 제외하는 이유
- 무겁다.
- PC 환경 의존성이 있다.
- 코드 리뷰 대상이 아니다.

대신 실행이 필요하면:
- 별도 런타임 폴더 공유
- release 패키지 전달
- 설치/부트스트랩 문서 제공

## 결론
- 개발 공유는 Git 저장소
- 실행 테스트는 release 패키지
- 코드와 문서는 같이 관리
