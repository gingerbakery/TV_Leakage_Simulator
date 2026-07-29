# Clone 환경 원클릭 실행 개선

## 문제

- 새로 clone한 저장소에는 Git에서 제외되는 `_tools`, `.venv`, `frontend/dist`, `frontend/node_modules`가 없다.
- 기존 `run_web.bat`은 `_tools/python313`만 사용해 사내 PC의 `.venv` 환경을 인식하지 못했다.
- PowerShell의 `npm` 별칭이나 프로필이 실제 `npm.cmd` 대신 오래된 8787 API 실행 명령을 호출할 수 있었다.
- UI production build가 없으면 API 루트의 상태 JSON만 표시된다.

## 변경

- `run_web.bat` 더블클릭 한 번으로 실행 흐름을 통합했다.
- Python 선택 순서는 `.venv` → `_tools/python313` → Python 3.13 기반 `.venv` 자동 생성이다.
- 최초 환경 생성 시 `requirements-dev.txt`를 자동 설치한다.
- PowerShell 별칭을 우회하기 위해 실제 `npm.cmd` 경로를 찾아 직접 호출한다.
- 최초 실행 시 frontend package를 설치하고 매 실행 시 최신 production UI를 빌드한다.
- API와 UI 통합 주소를 `http://127.0.0.1:8788/`로 고정한다.
- 서버 health 응답 후 기본 브라우저를 자동으로 연다.

## 사용자 실행 방법

1. 저장소를 clone한다.
2. Python 3.13과 Node.js가 설치되어 있는지 확인한다.
3. 프로젝트 루트의 `run_web.bat`을 더블클릭한다.
4. 최초 실행의 패키지 설치가 완료되면 브라우저가 자동으로 열린다.

## 제한

- Python과 Node.js 자체는 사내 설치 정책이 달라 자동 설치하지 않는다.
- 최초 의존성 설치는 사내 보안 검사와 네트워크 환경에 따라 시간이 걸릴 수 있다.
