# TV Leakage Simulator

TV 기구 개발 단계에서 발생 가능한 `빛샘(light leakage)`을 빠르게 예측하기 위한 전용 시뮬레이터입니다.  
목표는 기구 설계자도 광학 전용 상용 툴 없이 `ROI 선택 → gap/transform 설정 → 광원 배치 → 상대/대략 절대 밝기 비교`까지 수행할 수 있게 만드는 것입니다.

## 현재 범위
- V1 기준 경량 시뮬레이터
- 분광/시감도(M2/M3)는 보류
- CAD import, ROI 선택, gap/transform, 간략 ray tracing, 결과 시각화 중심

## 현재 제공 모드

### 1. 웹 UI 프로토타입
- 주요 파일: `run_web.py`
- 목적:
  - CAD import
  - ROI 선택
  - component / face 선택
  - transform preview / apply
  - material library UI mockup

실행 예시:

```powershell
python run_web.py
```

또는 내장 Python 런타임 사용 예시:

```powershell
.\_tools\python313\python.exe run_web.py
```

### 2. CLI 실행
- 주요 파일: `run.py`
- 목적:
  - 시뮬레이션 코어 검증
  - 기본 출력(JSON/CSV/PNG) 확인

예시:

```powershell
python run.py --rays 4000 --max-depth 2 --seed 42 --output outputs
```

### 3. 데스크톱 EXE 패키지
- 목적:
  - 더블클릭 기반 내부 시연
  - 웹 UI를 별도 브라우저 없이 내장 WebView 창에서 실행
- 관련 문서: `docs/desktop-exe-packaging.md`

## 저장소 구조
- `src/leakage_simulator/`
  - 코어 엔진, CAD import, ROI, gap, ray tracing, 렌더링
- `run_web.py`
  - 웹 UI 프로토타입의 메인 엔트리
- `desktop_launcher/`
  - 내장 WebView 데스크톱 런처 소스
- `docs/`
  - 요구사항, 설계, 협업 규칙, material 구조, 시작 가이드
- `samples/`
  - 소형 샘플 자산

## 주요 문서
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
- 따라서 다른 개발자가 clone만 해서는 즉시 실행되지 않을 수 있습니다.
- 실행이 바로 필요하면 아래 중 하나가 필요합니다:
  - 별도로 공유된 `_tools/` 런타임
  - 시스템 Python + 필요한 의존성 설치
  - `release/` 패키지 전달

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
