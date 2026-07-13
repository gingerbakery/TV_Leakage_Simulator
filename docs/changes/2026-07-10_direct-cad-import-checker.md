# Direct CAD Import Checker

## Why
- 회사 PC에서 웹서버 startup이 오래 걸리거나 보안 정책 때문에 localhost 구동 확인이 지연될 수 있다.
- 사용자는 지금 가장 먼저 `STEP/STP 도면이 실제로 import 되는지`만 빠르게 확인할 필요가 있다.

## Added
- `check_cad_import.py`
  - 웹서버 없이 direct import만 검사
  - `--cad` 미지정 시 파일 선택창 지원
  - 성공/실패/synthetic fallback 여부를 즉시 출력
  - `outputs/import_check/*.json`에 결과 저장
- `check_cad_import.bat`
  - 회사 PC에서 더블클릭/터미널 실행 가능한 간단 launcher

## Expected usage
- `.\check_cad_import.bat`
- 또는
- `.\_tools\python313\python.exe check_cad_import.py --cad C:\path\to\file.stp`
