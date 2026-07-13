# Lite Company Package Rebuild

## What happened
- 기존 `tv_leakage_web_test_v0.6.8.zip`는 압축 생성 중 timeout으로 인해 중앙 디렉터리가 완성되지 못했고, 결과적으로 손상된 zip이 만들어졌다.
- 따라서 Windows에서 `압축(ZIP) 폴더가 올바르지 않습니다` 오류가 발생했다.

## Fix
- 배포 패키지를 다시 구성하면서 `실제 웹 UI + STEP/STP import`에 필요한 최소 런타임만 남겼다.
- `CadQuery` 전체 스택 대신 `OCP direct STEP reader` 경로를 추가해 무거운 의존성 대부분을 제거했다.

## Lite runtime included
- Python embedded core
- `OCP`
- `cadquery_ocp.libs`
- `vtk.libs`
- `src\leakage_simulator`
- `run_web.py`, `run_web.bat`

## Runtime intentionally excluded
- `cadquery`
- `scipy`
- `numba`
- `llvmlite`
- `casadi`
- `matplotlib`
- `vtkmodules`
- 기타 개발용/출력용 폴더

## Validation
- 새 라이트 스테이지에서 `run_web.py` import 확인
- 새 라이트 스테이지에서 실제 `.stp` 파일 import 확인
- 최종 zip은 생성 후 `zipfile.testzip()`로 무결성 검사 예정
