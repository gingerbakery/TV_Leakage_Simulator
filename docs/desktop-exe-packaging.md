# 데스크톱 EXE 패키징 가이드

## 목적
- 브라우저 명령 입력 없이 더블클릭만으로 시뮬레이터를 실행할 수 있게 한다.
- 기존 `run_web.py` 기반 CAD import 흐름을 그대로 유지한다.
- 사내 시연 및 테스트 배포를 쉽게 만든다.

## 현재 방식
- `LeakageSimulator.exe`는 얇은 데스크톱 런처다.
- 런처는 내부적으로:
  - embedded Python 실행
  - `run_web.py` 서버 실행
  - local `127.0.0.1` 포트 대기
  - WebView2 창으로 UI 표시

## 장점
- 별도 브라우저를 열 필요가 없다.
- STEP/STP/X_T import 흐름을 Python 쪽 그대로 활용할 수 있다.
- 코딩 경험이 거의 없는 사용자도 더 쉽게 테스트 가능하다.

## 패키지 구성
- `LeakageSimulator.exe`
- `run_web.py`
- `src/`
- `_tools/python313/`
- WebView2 관련 DLL
- 필요 시 `samples/`, `_uploads/`, `outputs/`

## 제약 사항
- 패키지 크기가 크다.
  - 이유: CAD import용 Python/CAD 런타임 포함
- target PC에 WebView2 runtime이 필요할 수 있다.
- 이 방식은 `run_web.py`를 감싼 실행 패키지이지, 시뮬레이터 코어를 별도 재구현한 것은 아니다.

## 빌드
- 사용 스크립트: `build_desktop_webview_exe.bat`
- 출력 폴더: `release/leakage_simulator_desktop_v0.1`

## 운영 권장
- 개발 공유는 Git 저장소로 진행
- 실행 테스트/시연은 `release/` 패키지로 배포
