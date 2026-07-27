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

### 최소 런타임
- Python 3.13 embedded runtime
- FastAPI·Uvicorn·Pydantic
- `OCP`
- OCP가 직접 연결하는 CAD/VTK DLL dependency closure
- `NumPy`
- 전체 CadQuery, VTK Python module, SciPy, PyArrow, Jupyter 등은 제외

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
