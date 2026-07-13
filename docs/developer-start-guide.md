# 타 개발자 시작 가이드

## 목적
- 다른 개발자가 이 저장소를 clone 받은 뒤 빠르게 구조를 이해하고 작업을 시작할 수 있게 한다.

## 먼저 알아둘 점
- 이 저장소는 `소스코드/문서 중심`으로 관리된다.
- `_tools/` 런타임은 기본적으로 Git에 포함되지 않는다.
- 따라서 clone만으로 바로 실행되지 않을 수 있다.

## 문서 읽는 순서
1. `README.md`
2. `docs/requirements.md`
3. `docs/design.md`
4. `docs/backend-data-contracts.md`
5. `docs/developer-ownership.md`
6. 본인 담당 기능 관련 `docs/changes/*.md`

## 기능별 진입 포인트

### CAD import / ROI 담당
- `src/leakage_simulator/components.py`
- `src/leakage_simulator/roi.py`
- `run_web.py`의 ROI 관련 UI 구간

### Gap / Transform 담당
- `src/leakage_simulator/gap.py`
- `src/leakage_simulator/types.py`
- `run_web.py`의 transform UI 구간

### Ray trace / Brightness 담당
- `src/leakage_simulator/raytracer.py`
- `src/leakage_simulator/engine.py`
- `src/leakage_simulator/render.py`

### Material 담당
- `src/leakage_simulator/materials.py`
- `run_web.py`의 material UI 구간
- `docs/material-library*.md`

### Desktop packaging 담당
- `desktop_launcher/`
- `build_desktop_webview_exe.bat`

## 실행 방법

### 1. 개발용
- 시스템 Python이 준비되어 있으면:

```powershell
python run_web.py
```

### 2. 별도 런타임이 있을 때

```powershell
.\_tools\python313\python.exe run_web.py
```

### 3. 테스트/시연용
- release 패키지가 있으면 EXE 더블클릭

## 작업 원칙
- 코드 변경 시 관련 문서도 함께 갱신
- 공용 타입 변경 시 `docs/changes/*.md` 기록
- 한 기능을 수정하더라도 다른 모듈 책임까지 끌고 가지 않기

## 권장 브랜치
- `feature/cad-import-roi`
- `feature/transform-gap`
- `feature/material-library`
- `feature/desktop-packaging`

## 첫 체크리스트
- 저장소 clone
- 문서 읽기
- 본인 담당 파일 경계 확인
- 실행 환경 확인
- 작은 수정부터 시작
