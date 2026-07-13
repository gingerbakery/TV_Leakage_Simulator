# 개발자 기능 소유 경계 가이드

## 목적
- 여러 개발자가 ROI, Gap, Ray trace, UI를 병렬로 작업할 때 역할 충돌을 줄인다.

## 기준 문서
- 요구사항: `docs/requirements.md`
- 아키텍처: `docs/design.md`
- 데이터 계약: `docs/backend-data-contracts.md`
- 변경 이력: `docs/changes/*.md`

## 기능별 담당 범위

### 1. ROI / CAD scene 구성
- 주요 파일:
  - `src/leakage_simulator/components.py`
  - `src/leakage_simulator/roi.py`
  - `run_web.py`의 ROI UI 구간
- 담당 내용:
  - component/face 분해
  - ROI face 해석
  - receiver 구성
  - ROI 선택 방식(3D view / component / 공간 선택)

### 2. Gap / Transform
- 주요 파일:
  - `src/leakage_simulator/gap.py`
  - `src/leakage_simulator/types.py`
  - `run_web.py`의 transform UI 구간
- 담당 내용:
  - component move
  - local face move
  - tilt
  - 공차/전달율 모델

### 3. Ray trace / Brightness
- 주요 파일:
  - `src/leakage_simulator/raytracer.py`
  - `src/leakage_simulator/engine.py`
  - `src/leakage_simulator/render.py`
- 담당 내용:
  - 광선 추적
  - 반사/감쇠
  - receiver 누적
  - `Nits_est`

### 4. Material / Optical library
- 주요 파일:
  - `src/leakage_simulator/materials.py`
  - `run_web.py`의 material UI 구간
  - 관련 문서 `docs/material-library*.md`

### 5. Desktop packaging
- 주요 파일:
  - `desktop_launcher/`
  - `build_desktop_webview_exe.bat`

## 작업 원칙
- 공용 타입 변경 시 문서 이력도 같이 남긴다.
- ROI 담당자는 가능하면 `roi.py`와 ROI UI 구간 중심으로 수정한다.
- Gap 담당자는 receiver 계산 책임을 가져가지 않는다.
- Ray trace 담당자는 ROI 선택 방식 자체를 해석하지 않는다.

## 변경 기록 권장 규칙
- ROI: `docs/changes/YYYY-MM-DD_roi-*.md`
- Gap: `docs/changes/YYYY-MM-DD_gap-*.md`
- Ray trace: `docs/changes/YYYY-MM-DD_raytrace-*.md`
- 공용 경계/타입: `docs/changes/YYYY-MM-DD_backend-contract-*.md`
