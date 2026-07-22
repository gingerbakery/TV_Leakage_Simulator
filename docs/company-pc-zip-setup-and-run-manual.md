# GitHub ZIP 기반 사내 개발 환경 설정 및 실행 매뉴얼

## 1. 목적

사외 GitHub 저장소의 소스 코드를 ZIP 파일로 내려받아 사내 PC로 옮긴 뒤, Python 개발 환경을 구성하고 TV Leakage Simulator의 CAD import 및 Web UI를 실행하는 절차를 설명한다.

대상 저장소:

- `https://github.com/gingerbakery/TV_Leakage_Simulator`
- 기준 브랜치: `main`

> GitHub의 `Download ZIP`에는 GitHub에 커밋하고 push한 파일만 포함된다. 사외 PC에만 있는 미커밋 변경 사항은 포함되지 않는다.

## 2. 준비 프로그램

사내 PC에 다음 프로그램이 필요하다.

- Python 3.13 계열 64비트(검증 버전: Python 3.13.3)
- Visual Studio Code
- Microsoft Edge 또는 Google Chrome

Python 설치 시 `Add python.exe to PATH` 항목을 선택한다. 설치 후에는 Visual Studio Code를 종료했다가 다시 실행한다.

## 3. 소스 코드 다운로드 및 전달

사외 인터넷 PC에서 다음 순서로 진행한다.

1. GitHub 저장소에 접속한다.
2. 브랜치가 `main`인지 확인한다.
3. `Code` 버튼을 누른다.
4. `Download ZIP`을 선택한다.
5. 다운로드한 `TV_Leakage_Simulator-main.zip`을 회사에서 허용한 보안 절차와 전달 수단으로 사내 PC에 옮긴다.

## 4. 압축 해제 및 프로젝트 열기

1. 사내 PC에서 작업 폴더를 만든다.

   ```text
   C:\Work\TV_Leakage_Simulator
   ```

   해당 위치에 쓰기 권한이 없으면 사용자 문서 폴더 아래에 만든다.

   ```text
   C:\Users\사용자이름\Documents\TV_Leakage_Simulator
   ```

2. ZIP 파일의 `속성`에서 `차단 해제`가 보이면 선택한 후 압축을 푼다.
3. Visual Studio Code에서 `File` → `Open Folder`를 선택한다.
4. 다음 파일이 직접 들어 있는 폴더를 연다.

   ```text
   run_web.py
   check_cad_import.py
   requirements-dev.txt
   src
   samples
   ```

5. `Terminal` → `New Terminal`을 선택하여 PowerShell 터미널을 연다.

## 5. 최초 Python 환경 설정

아래 명령은 Visual Studio Code에서 연 프로젝트 폴더의 터미널에 한 줄씩 입력한다.

### 5.1 Python 버전 확인

```powershell
python --version
```

정상 예시:

```text
Python 3.13.3
```

### 5.2 프로젝트 전용 가상환경 생성

```powershell
python -m venv .venv
```

### 5.3 필수 패키지 설치

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### 5.4 OCP 설치 확인

```powershell
.\.venv\Scripts\python.exe -c "import cadquery, OCP; print('OCP OK')"
```

다음 메시지가 나오면 정상이다.

```text
OCP OK
```

> 이미 활성화된 Python 환경에서 `python` 명령으로 OCP 확인이 성공했다면, 이후 명령의 `.\.venv\Scripts\python.exe` 대신 `python`을 사용해도 된다.

## 6. CAD import 기능 확인

프로젝트에 포함된 STEP 샘플 파일로 실제 CAD import를 검사한다.

```powershell
.\.venv\Scripts\python.exe check_cad_import.py --cad ".\samples\tv_leakage_full_assembled_no_gap.stp" --output-dir ".\outputs" --no-dialog
```

다음 메시지가 나오면 CAD import 기능이 정상이다.

```text
[OK] Real CAD import succeeded
```

## 7. Web UI 실행

다음 명령을 입력한다.

```powershell
.\.venv\Scripts\python.exe -u run_web.py --port 8788
```

이미 `python` 명령으로 환경 검증을 마친 경우에는 다음과 같이 실행해도 된다.

```powershell
python -u run_web.py --port 8788
```

정상 실행 예시:

```text
run web ui v0.9.11 at http://127.0.0.1:8788
health: http://127.0.0.1:8788/health
Press Ctrl + C to stop
```

서버가 실행 중인 터미널은 닫지 않는다. Edge 또는 Chrome을 열어 다음 주소로 접속한다.

```text
http://127.0.0.1:8788
```

상태 확인 주소:

```text
http://127.0.0.1:8788/health
```

정상 응답 예시:

```text
ok web_ui_version=0.9.11
```

8788 포트가 이미 사용 중이면 프로그램이 8789 등 다른 포트를 선택할 수 있다. 이 경우 터미널에 실제로 표시된 주소로 접속한다.

## 8. Web UI에서 CAD 불러오기

1. Web UI에서 CAD 불러오기 기능을 선택한다.
2. 다음 샘플 파일을 선택한다.

   ```text
   samples\tv_leakage_full_assembled_no_gap.stp
   ```

3. 모델 형상과 부품 목록이 화면에 표시되는지 확인한다.

## 9. 프로그램 종료 및 재실행

### 종료

서버가 실행 중인 터미널을 클릭하고 `Ctrl + C`를 누른다.

### 다음 사용 시 재실행

Visual Studio Code에서 프로젝트 폴더와 새 터미널을 연 뒤 다음 명령만 실행한다.

```powershell
.\.venv\Scripts\python.exe -u run_web.py --port 8788
```

## 10. 사내 인터넷이 차단된 경우

사외의 Windows 64비트·Python 3.13 환경에서 필요한 패키지를 미리 다운로드한다.

```powershell
mkdir wheelhouse
python -m pip download -r requirements-dev.txt -d wheelhouse
```

생성된 `wheelhouse` 폴더를 프로젝트와 함께 사내로 전달한 후 사내 PC에서 설치한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse -r requirements-dev.txt
```

회사 내부 Python 패키지 저장소가 있다면 사내 IT 지침을 우선 적용한다.

## 11. 주의 사항

- GitHub ZIP에는 `.git` 폴더가 없으므로 `git pull`, `git commit`, `git push`를 바로 사용할 수 없다.
- `_tools` 폴더는 Git에서 제외되어 있으므로 GitHub ZIP에 포함되지 않는다.
- 현재 `run_web.bat`는 `_tools\python313\python.exe`를 사용하므로 ZIP만 받은 환경에서는 실패할 수 있다.
- ZIP만 받은 환경에서는 이 문서의 `.\.venv\Scripts\python.exe` 명령을 사용한다.
- Windows 보안 경고가 발생하면 임의로 보안 기능을 해제하지 말고 사내 IT 승인 절차를 따른다.
- 서버를 사용하는 동안 실행 터미널을 닫지 않는다.

## 12. 빠른 실행 요약

최초 한 번:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -c "import cadquery, OCP; print('OCP OK')"
.\.venv\Scripts\python.exe check_cad_import.py --cad ".\samples\tv_leakage_full_assembled_no_gap.stp" --output-dir ".\outputs" --no-dialog
```

매번 실행:

```powershell
.\.venv\Scripts\python.exe -u run_web.py --port 8788
```

브라우저 접속:

```text
http://127.0.0.1:8788
```
