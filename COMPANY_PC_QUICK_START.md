# Company PC Quick Start

## 목적
- 회사 PC에서 `웹 UI 실행 테스트`만 빠르게 확인하기 위한 최소 패키지 안내

## 실행 방법
1. 압축 파일을 회사 PC의 짧은 경로에 풉니다.
   - 예: `C:\TV_leakage_simulator`
2. STEP/STP import만 빠르게 확인하려면 `check_cad_import.bat`를 먼저 실행합니다.
3. 웹 UI가 필요하면 `run_web.bat`를 실행합니다.
4. 터미널 창을 닫지 말고 유지합니다.
5. 브라우저에서 아래 주소로 접속합니다.
   - `http://127.0.0.1:8787`
   - 포트가 바뀌면 터미널 로그에 표시된 주소로 접속

## 포함 파일
- `run_web.bat`
- `run_web.py`
- `check_cad_import.bat`
- `check_cad_import.py`
- `src\leakage_simulator\...`
- `_tools\python313\...`
- `samples\demo_tv_frame.obj`

## 주의
- 이 패키지는 `실행 테스트용 최소 세트`입니다.
- 개발용 자동 재시작은 포함하지 않았습니다.
- 회사 PC에서 코드를 수정하지 않는다면 `start_web_dev.bat`는 필요하지 않습니다.
- 실제 STEP/STP import를 위해 embedded Python 런타임과 CAD 관련 라이브러리를 함께 포함합니다.
- 웹서버가 오래 걸리거나 막히는 환경에서는 `check_cad_import.bat`로 import 성공 여부부터 먼저 확인하세요.

## 권장 경로
- 가능하면 공백/한글/특수문자가 너무 많은 경로는 피합니다.
- 예:
  - `C:\TV_leakage_simulator`
