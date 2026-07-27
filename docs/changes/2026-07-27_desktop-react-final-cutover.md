# React 데스크톱 최종 전환

- 날짜: 2026-07-27
- 대상 브랜치: `codex/framework-migration`
- 로드맵: Step 13

## 최종 실행 구조

1. `LeakageSimulator.exe`가 embedded Python으로 `run_web.py`를 실행한다.
2. `run_web.py`는 `run_api.py`의 FastAPI·Uvicorn 서버를 시작한다.
3. FastAPI가 `/`와 `/assets`에서 React production build를 제공한다.
4. 동일 origin의 `/api`, `/outputs`, `/health`가 계산과 결과를 제공한다.
5. WebView2가 이 통합 주소를 표시한다.

개발 모드에서는 기존처럼 Vite `5173`과 FastAPI `8787`을 분리해 HMR을
사용할 수 있다. 배포 모드에서는 단일 localhost 포트만 사용한다.

## 레거시 처리

- 기존 인라인 HTML·JavaScript 서버는 `run_web_legacy.py`로 이동했다.
- 최종 `run_web.py`는 새 통합 서버의 작은 호환 진입점이다.
- `run_web_legacy.py`와 기존 `web/static`은 v1.0.0 배포물에 포함하지 않는다.
- 필요하면 Git 이력과 레거시 파일에서 이전 구현을 확인할 수 있다.

## 패키지

- 폴더:
  `release/leakage_simulator_desktop_v1.0.0_lite/`
- ZIP:
  `release/leakage_simulator_desktop_v1.0.0_lite.zip`
- SHA-256:
  `release/leakage_simulator_desktop_v1.0.0_lite.zip.sha256`
- 빌드 결과:
  - 압축 해제 폴더 353.5 MB
  - ZIP 98.5 MB

`release/`는 생성물이며 Git에는 포함하지 않는다.

## 검증

- React TypeScript production build
- FastAPI·Uvicorn 최소 런타임 import
- 106,352-face / 54,191-vertex / 4-component STEP Import
- Python 회귀 테스트 84개
- ZIP 필수 파일 및 React production index 검사
- ZIP 재해제 후 STEP Import 재검사
- 압축본 내부 FastAPI health와 React HTML·JS asset 응답
- `LeakageSimulator.exe`가 내장 서버를 시작하고 health check를 통과하는지
  실제 실행 확인

최초 빌드에서 PERF-3A 테스트가 보고서용 Matplotlib를 모듈 import 시점에
요구하는 결합을 발견했다. 보고서 생성 함수 안에서만 Matplotlib를 지연
import하도록 바꿔, Matplotlib를 제외한 경량 계산 런타임에서도 핵심 회귀가
독립적으로 실행되게 했다.
