# 개발 환경 가이드

## 목적
- 여러 개발자가 같은 Python/모듈 기준으로 작업할 수 있게 환경 기준을 고정한다.
- 회사 PC나 다른 개발자 PC에서도 같은 방식으로 개발/테스트를 시작할 수 있게 한다.

## 현재 기준 개발 환경

### Python
- 권장 버전: `Python 3.13.3`

### 핵심 패키지
- `cadquery==2.8.0`
- `cadquery-ocp==7.9.3.1.1`
- `matplotlib==3.11.0`
- `numpy==2.4.6`
- `pillow==12.3.0`

이 버전 기준으로 현재 저장소가 검증되었다.

## 공유 방식 권장안

### 방식 A. 가장 쉬운 방식: 내장 런타임 공유
- 현재 프로젝트는 `_tools/python313/` 내장 Python 런타임을 기준으로 검증되었다.
- 이 폴더는 Git에는 넣지 않고, 별도 폴더/압축파일/사내 공유 드라이브로 전달한다.

장점:
- 설치 오차가 적다.
- STEP/STP import 관련 런타임 차이를 줄일 수 있다.
- 회사 PC/다른 개발자 PC에서 같은 결과를 보기 쉽다.

단점:
- 용량이 크다.
- Git 저장소만 clone해서는 바로 실행되지 않는다.

### 방식 B. 시스템 Python 설치 + requirements 설치
- 각 개발자가 자기 PC에 Python 3.13 계열을 설치하고,
- `requirements-dev.txt` 기준으로 패키지를 설치한다.

장점:
- Git clone 후 환경만 만들면 된다.
- `_tools/`를 따로 들고 다니지 않아도 된다.

단점:
- CAD/CQ/OCP 계열 패키지 설치가 환경마다 조금 다를 수 있다.
- 버전이 틀어지면 import나 tessellation 동작 차이가 날 수 있다.

## 회사 PC 권장안
- 회사 PC에서 **안정적으로 이어서 개발**하려면 우선순위는 아래가 좋다:

1. 가능하면 `_tools/python313/` 런타임을 그대로 복사해 사용
2. 그게 어렵다면 Python 3.13 설치 후 `requirements-dev.txt`로 환경 구성

## 설치 절차

### A안: 내장 런타임 복사 방식
1. 프로젝트 폴더 외에 `_tools/python313/` 폴더를 함께 복사
2. 아래처럼 실행

```powershell
.\_tools\python313\python.exe --version
if (-not (Test-Path .\frontend\dist\index.html)) {
    npm --prefix frontend install
    npm --prefix frontend run build
}
.\_tools\python313\python.exe run_web.py
```

### B안: 시스템 Python 방식
1. Python 3.13 설치
2. 프로젝트 루트에서 아래 실행

```powershell
python --version
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

3. 실행 확인

```powershell
npm --prefix frontend install
npm --prefix frontend run build
python run_web.py
```

## 권장 확인 항목
- Python 버전 확인

```powershell
python --version
```

- 핵심 패키지 확인

```powershell
python -c "import cadquery, fastapi, OCP, numpy, uvicorn; print(cadquery.__version__)"
```

## 다른 개발자에게 공유할 때 꼭 같이 알려줄 내용
- 이 저장소는 `_tools/`를 Git에 포함하지 않음
- 따라서 아래 중 하나가 반드시 필요함
  - `_tools/python313/` 별도 공유
  - Python 3.13 + `requirements-dev.txt` 설치
- CAD import 테스트가 필요하면 `cadquery` / `cadquery-ocp`가 반드시 맞아야 함

## 권장 운영
- 개발 환경 기준은 이 문서와 `requirements-dev.txt`를 기준으로 관리
- 환경 버전이 바뀌면 `docs/changes/*.md`에 기록
- 가능하면 한 팀 안에서는 Python minor version까지 맞춘다

## 한 줄 결론
- 가장 안전한 공유 방식은 `_tools/python313/` 런타임 별도 공유
- 가장 일반적인 공유 방식은 `Python 3.13 + requirements-dev.txt`
