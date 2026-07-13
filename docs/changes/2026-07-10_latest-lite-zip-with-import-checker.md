# Latest Lite Zip with Import Checker

## Why
- 회사 PC에서는 웹서버보다 `STEP/STP import가 실제로 되는지`를 먼저 확인할 필요가 있었다.
- 기존 lite zip에는 `check_cad_import.py` / `check_cad_import.bat`가 포함되어 있지 않았다.

## What changed
- 최신 라이트 배포 패키지에 아래를 추가 포함
  - `check_cad_import.py`
  - `check_cad_import.bat`
  - 업데이트된 `COMPANY_PC_QUICK_START.md`

## Packaging intent
- 웹 UI 테스트와 direct CAD import 검사를 모두 한 패키지에서 수행
- 회사 PC에서는
  - 먼저 `check_cad_import.bat`
  - 이후 필요 시 `run_web.bat`
  순서로 점검 가능
